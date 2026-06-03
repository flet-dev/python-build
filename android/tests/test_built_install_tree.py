"""Post-build tests against the actual generated `install/` tree.

Complement to the source-only `test_normalize_mobile_forge_install.py`
unit tests: those exercise `append_relocation_block()` in isolation
against a stub sysconfigdata under a tempdir; the tests here read the
real sysconfigdata that `android/build-all.sh` just produced and check
the shipped artifact matches our expectations.

Driven by `MOBILE_FORGE_INSTALL_TREE` from the workflow's post-build
test step. ABIs are version-dependent — 3.12 ships 4, 3.13+ ships 2 —
so tests iterate whatever's actually on disk rather than hardcoding.
"""

import os
import re
import unittest
from pathlib import Path

from _testlib import post_build, INSTALL_TREE, PYTHON_VERSION_SHORT


def _sysconfigdata_files(install_root: Path):
    """Yield (abi, sysconfigdata path) for every ABI present under
    `install_root/android/<abi>/python-X.Y.Z/lib/pythonX.Y/_sysconfigdata__linux_.py`.
    """
    android_root = install_root / "android"
    if not android_root.is_dir():
        return
    for abi_dir in sorted(android_root.iterdir()):
        if not abi_dir.is_dir():
            continue
        # python-X.Y.Z dir name is version-dependent; glob for it.
        for py_dir in abi_dir.glob(f"python-{PYTHON_VERSION_SHORT}.*"):
            sd = (
                py_dir
                / "lib"
                / f"python{PYTHON_VERSION_SHORT}"
                / "_sysconfigdata__linux_.py"
            )
            if sd.is_file():
                yield abi_dir.name, sd


@post_build
class BuiltSysconfigdataShape(unittest.TestCase):
    """For every ABI in the install tree, the shipped sysconfigdata must
    have the relocator block applied and the substitution rules in the
    shape the source code is meant to produce.
    """

    @classmethod
    def setUpClass(cls):
        # Resolved at import time by _testlib; bail with a clear message
        # if the test was selected without the env var (unusual; the
        # decorator already gates on it, but be explicit).
        if not INSTALL_TREE:
            raise unittest.SkipTest(
                "MOBILE_FORGE_INSTALL_TREE not set — the workflow's post-build "
                "test step is responsible for populating it."
            )
        cls.install_root = Path(INSTALL_TREE)
        cls.found = list(_sysconfigdata_files(cls.install_root))

    def test_at_least_one_sysconfigdata_present(self):
        """Sanity: build-all.sh produced something for at least one ABI.

        If this fails, every later test would skip trivially — surfacing
        the empty-tree case as its own failure is clearer.
        """
        self.assertGreater(
            len(self.found),
            0,
            f"No _sysconfigdata__linux_.py files found under {self.install_root}/android/. "
            f"Either build-all.sh produced nothing, or MOBILE_FORGE_INSTALL_TREE "
            f"points at the wrong dir.",
        )

    def test_relocator_block_present(self):
        """`build-all.sh` must invoke `normalize_mobile_forge_install.py`
        so the relocator block lands in every shipped sysconfigdata. If
        the call gets removed/skipped, this test catches it.
        """
        for abi, sd in self.found:
            with self.subTest(abi=abi):
                self.assertIn(
                    "# mobile-forge sysconfig relocation",
                    sd.read_text(),
                    msg=(
                        f"Relocator block missing from {sd} — did build-all.sh "
                        f"skip the normalize_mobile_forge_install.py invocation?"
                    ),
                )

    def test_install_prefixes_no_usr_local(self):
        """Regression guard for #8 at the *shipped artifact* level — complement to the
        unit-test coverage in test_normalize_mobile_forge_install.py.

        If `_install_prefixes` ever ships with `"/usr/local"` in the tuple again,
        mobile-forge CI on GitHub runners (NDK_HOME under /usr/local) will fail at
        crossenv create — silently, with no captured stderr. This catches it before
        the artifact escapes python-build's CI.
        """
        for abi, sd in self.found:
            with self.subTest(abi=abi):
                m = re.search(r"_install_prefixes\s*=\s*\(([^)]*)\)", sd.read_text())
                self.assertIsNotNone(
                    m, msg=f"_install_prefixes tuple not found in {sd}"
                )
                contents = m.group(1)
                self.assertNotIn(
                    "/usr/local",
                    contents,
                    msg=(
                        f"/usr/local re-introduced into _install_prefixes in {sd} "
                        f"(value: {contents!r}). See fix/relocator-drops-usr-local-prefix "
                        f"for context."
                    ),
                )

    def test_build_ndk_baked(self):
        """`_build_ndk` must be a non-empty path string. If `''` or `None`
        ships, the NDK substitution rule no-ops on the consumer and
        CC/CXX paths stay as Linux-CI-runner paths — the original 3.13+
        macOS bug that PR #8 added an extra `$toolchain` fallback to
        fix. This guards against a regression of that fallback chain.
        """
        for abi, sd in self.found:
            with self.subTest(abi=abi):
                m = re.search(r"_build_ndk\s*=\s*(.+)$", sd.read_text(), re.MULTILINE)
                self.assertIsNotNone(m, msg=f"_build_ndk assignment not found in {sd}")
                val = m.group(1).strip()
                self.assertNotIn(
                    val,
                    ("''", '""', "None"),
                    msg=(
                        f"_build_ndk is empty in {sd} — android/build.sh failed "
                        f"to resolve $toolchain for Python {PYTHON_VERSION_SHORT}. "
                        f"Check the $toolchain detection fallback chain."
                    ),
                )


if __name__ == "__main__":
    unittest.main()
