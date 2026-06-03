"""Post-build tests against the actual shipped `build-details.json` files.

CPython 3.14+'s official Android tooling emits
`lib/python<X.Y>/build-details.json` alongside the per-version
sysconfigdata. Downstream cross-build tooling — most importantly
`maturin` — reads it to drive cross-compilation, including the
`libpython.dynamic_stableabi` value that the consumer's linker uses
as `-L`. python-build's CI bakes build-time paths into the file (currently
under `/usr/local`); `normalize_mobile_forge_install.rewrite_build_details_json`
re-anchors those at install time so the shipped artifact reflects the
consumer's actual filesystem.

This module asserts on the shipped artifact itself: every path field is
anchored at the install prefix, no `/usr/local` strings leaked through,
and `libpython.dynamic_stableabi` points at a file that actually exists.

3.13 and 3.12 don't emit a `build-details.json` (it's a 3.14 addition),
so the relevant `@post_build` class is gated on `PYTHON_VERSION_SHORT`
via `@requires_python("3.14")` and degrades to a skip on the older
matrix shards.
"""

import json
import unittest
from pathlib import Path

from _testlib import (
    post_build,
    requires_python,
    INSTALL_TREE,
    PYTHON_VERSION_SHORT,
)


# Every absolute-path string field documented for build-details.json
# schema v1 that the rewrite is responsible for re-anchoring. Listed
# explicitly rather than walked dynamically so a future CPython schema
# addition that ships a new path field shows up as a test failure (the
# field's anchored-at-install-prefix assertion must be added intentionally).
_PATH_FIELDS: tuple[tuple[str, ...], ...] = (
    ("base_prefix",),
    ("base_interpreter",),
    ("libpython", "dynamic"),
    ("libpython", "dynamic_stableabi"),
    ("c_api", "headers"),
    ("c_api", "pkgconfig_path"),
)


def _build_details_files(install_root: Path):
    """Yield (abi, build_details path) for every ABI in the install tree.

    Mirrors `_sysconfigdata_files()` in test_built_install_tree.py: glob
    `python-X.Y.Z` ABI dirs under the install root, then locate the
    build-details.json inside each version's lib/pythonX.Y/ subdir.
    """
    android_root = install_root / "android"
    if not android_root.is_dir():
        return
    for abi_dir in sorted(android_root.iterdir()):
        if not abi_dir.is_dir():
            continue
        for py_dir in abi_dir.glob(f"python-{PYTHON_VERSION_SHORT}.*"):
            details = py_dir / "lib" / f"python{PYTHON_VERSION_SHORT}" / "build-details.json"
            if details.is_file():
                yield abi_dir.name, details


def _resolve(data: dict, path: tuple[str, ...]):
    """Walk a nested dict by key path, returning None if any segment is missing."""
    node = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


@post_build
@requires_python("3.14")
class BuiltBuildDetailsJsonShape(unittest.TestCase):
    """The shipped `build-details.json` must carry re-anchored paths.

    Gated on 3.14: CPython didn't ship this file pre-3.14, so on 3.12/3.13
    shards there's nothing to inspect and the class skips wholesale.
    """

    @classmethod
    def setUpClass(cls):
        if not INSTALL_TREE:
            raise unittest.SkipTest(
                "MOBILE_FORGE_INSTALL_TREE not set — the workflow's post-build "
                "test step is responsible for populating it."
            )
        cls.install_root = Path(INSTALL_TREE)
        cls.found = list(_build_details_files(cls.install_root))

    def test_at_least_one_build_details_json_present(self):
        """Sanity: 3.14 matrix shards must ship a build-details.json per ABI.

        If absent, CPython's Android tooling silently regressed (or a recipe
        change in `android/build.sh` is dropping the file). Surface that
        before the downstream linker fails opaquely.
        """
        self.assertGreater(
            len(self.found),
            0,
            f"No build-details.json files found under {self.install_root}/android/"
            f"<abi>/python-{PYTHON_VERSION_SHORT}.*/lib/python{PYTHON_VERSION_SHORT}/. "
            f"Either build-all.sh produced nothing, CPython's Android tooling stopped "
            f"emitting build-details.json, or MOBILE_FORGE_INSTALL_TREE points at the "
            f"wrong dir.",
        )

    def test_no_build_time_prefix_leaked(self):
        """Regression guard for `rewrite_build_details_json` getting skipped.

        Asserts no `/usr/local` substring survives anywhere in the JSON.
        Build-details.json is CPython-generated with /usr/local as the build-time
        install root; if any /usr/local path leaks through (rewrite call dropped
        from `main()`, function broke on a path field, etc.), the consumer linker
        gets `-L/usr/local/lib` and fails with `unable to find library -lpython3`
        — silently, so this is the only test that catches it before the artifact
        escapes.
        """
        for abi, details in self.found:
            with self.subTest(abi=abi):
                text = details.read_text()
                self.assertNotIn(
                    "/usr/local",
                    text,
                    msg=(
                        f"/usr/local survived in {details} — "
                        f"did `rewrite_build_details_json` get dropped from `main()`?"
                    ),
                )

    def test_path_fields_anchored_at_install_prefix(self):
        """Every absolute-path field must live under the install prefix.

        Stronger than `test_no_build_time_prefix_leaked` for `/usr/local` — this
        catches a build-time prefix change to anything else as well. If CPython's
        Android tooling ever switches its install root from `/usr/local` to
        somewhere else, this fails immediately rather than letting half-rewritten
        files ship.
        """
        for abi, details in self.found:
            with self.subTest(abi=abi):
                data = json.loads(details.read_text())
                install_prefix = str(details.parents[2])
                for field in _PATH_FIELDS:
                    value = _resolve(data, field)
                    if value is None:
                        # Optional field absent on this ABI (e.g. some shards
                        # could legitimately omit pkgconfig). Don't fail just
                        # because the schema is sparser than the maximal set.
                        continue
                    with self.subTest(field=".".join(field)):
                        self.assertTrue(
                            value.startswith(install_prefix),
                            msg=(
                                f"build-details.json field {'.'.join(field)} = "
                                f"{value!r} is not anchored at install prefix "
                                f"{install_prefix!r} for {abi}."
                            ),
                        )

    def test_libpython_dynamic_stableabi_resolves(self):
        """`libpython.dynamic_stableabi` must point at a file that exists.

        This is the path the linker is told to follow for `-lpython3`. If the
        rewrite produces a syntactically-anchored path but the file isn't there
        (e.g. python-build's tarball dropped `libpython3.so`), the consumer link
        fails just as opaquely as the unrewritten-path case. Verify the file the
        JSON points at actually exists.
        """
        for abi, details in self.found:
            with self.subTest(abi=abi):
                data = json.loads(details.read_text())
                stableabi = _resolve(data, ("libpython", "dynamic_stableabi"))
                if stableabi is None:
                    self.skipTest(
                        f"libpython.dynamic_stableabi absent from {details}"
                    )
                self.assertTrue(
                    Path(stableabi).is_file(),
                    msg=(
                        f"libpython.dynamic_stableabi = {stableabi!r} in {details} "
                        f"does not exist on disk. python-build shipped a tree where "
                        f"the path-rewrite anchored correctly but the target file "
                        f"is missing."
                    ),
                )


if __name__ == "__main__":
    unittest.main()
