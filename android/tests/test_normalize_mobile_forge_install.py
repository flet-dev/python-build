"""Pre-build tests for android/normalize_mobile_forge_install.py — the
sysconfig relocator that gets appended to `_sysconfigdata__linux_.py`
so the build products can be re-anchored on a consumer host.

Each test renders the relocation block into a stub sysconfigdata file
under a tempdir, builds a controlled fake-filesystem layout for the
consumer-side NDK lookup, exec's the rendered file as a Python module,
and asserts what `build_time_vars` contains afterwards.
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the sibling module importable (android/normalize_mobile_forge_install.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from normalize_mobile_forge_install import append_relocation_block  # noqa: E402

from _testlib import pre_build  # noqa: E402


# ---------------------------------------------------------------------------
# Build-time path constants. Treated as opaque strings; the only requirement
# is that they contain the substrings the relocator scans for ("toolchains"
# inside _build_ndk, etc.). They do NOT need to exist on the test runner's
# filesystem — only the *consumer* side does.

# 3.12 path: python-build/CI's android/build.sh sources android-env.sh, which
# installs the NDK at $HOME/ndk/<letter>/, so the build-time NDK string ends
# up like this.
_BUILD_NDK_312 = "/home/runner/ndk/r27d/toolchains/llvm/prebuilt/linux-x86_64"

# 3.13+ path: CPython's in-tree Android tooling auto-resolves the NDK from
# $ANDROID_HOME/ndk/<version>/, which on a GitHub runner lives under
# /usr/local/lib/android/sdk/ — the build-time NDK string then starts with /usr/local.
_BUILD_NDK_313 = (
    "/usr/local/lib/android/sdk/ndk/27.3.13750724/toolchains/llvm/prebuilt/linux-x86_64"
)

_BUILD_PREFIX = "/home/runner/work/python-build/python-build/install/android/arm64-v8a/python-3.12.12"


# ---------------------------------------------------------------------------
# Fixture helpers


def _make_consumer_install(tmp: Path) -> Path:
    """Build the layout the relocator expects under `parents[2]`.

    Returns the path the stub _sysconfigdata__linux_.py should be written to. The
    relocator computes `_prefix = parents[2]` from that file's location, so
    `<tmp>/install/android/<abi>/python-3.12.12` ends up as the consumer's install prefix.
    """
    sd_dir = (
        tmp
        / "install"
        / "android"
        / "arm64-v8a"
        / "python-3.12.12"
        / "lib"
        / "python3.12"
    )
    sd_dir.mkdir(parents=True)
    return sd_dir / "_sysconfigdata__linux_.py"


def _make_consumer_ndk(ndk_home: Path, host_triple: str) -> Path:
    """Build a fake `<ndk_home>/toolchains/llvm/prebuilt/<host_triple>/bin/` tree.

    Returns the toolchain dir — the path the relocator's `_local_toolchain()` will yield.
    """
    toolchain = ndk_home / "toolchains" / "llvm" / "prebuilt" / host_triple
    (toolchain / "bin").mkdir(parents=True)
    return toolchain


def _render_and_exec(
    sd_path: Path,
    *,
    build_prefix: str,
    build_ndk: str,
    baked_vars: dict,
    env: dict,
) -> dict:
    """Append the relocation block to a stub sysconfigdata file, exec it
    under a controlled environment, and return the resulting build_time_vars.
    """
    sd_path.write_text(f"build_time_vars = {baked_vars!r}\n")
    append_relocation_block(sd_path, Path(build_prefix), build_ndk)

    orig_env = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("_t_sd", sd_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return dict(mod.build_time_vars)
    finally:
        os.environ.clear()
        os.environ.update(orig_env)


# ---------------------------------------------------------------------------
# Tests


@pre_build
class RelocatorRegressionTests(unittest.TestCase):
    """The relocator runs at sysconfigdata import time on the consumer.
    The regressions captured here all manifested as silent
    `subprocess.CalledProcessError` from crossenv: the consumer reads a CC/CXX path
    out of sysconfigdata, the path is mangled, the binary doesn't exist, crossenv exits
    ENOENT, the wrapper raises with no captured stderr.
    """

    def test_mobile_forge_runner_ndk_under_usr_local(self):
        """The PR #8 regression. When NDK_HOME points at a path containing
        `/usr/local` (mobile-forge CI on GitHub runners), the relocator must produce a
        CC value that contains `/usr/local` verbatim — NOT rewritten to the
        consumer's python install prefix.
        """
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str).resolve()
            sd_path = _make_consumer_install(tmp)
            consumer_prefix = sd_path.parents[2]  # .../python-3.12.12

            # Mimic a GitHub runner exposing NDK at .../usr/local/lib/android/sdk/ndk/<ver>
            # The literal substring "/usr/local/" inside the path is what triggered the bug.
            consumer_ndk = (
                tmp
                / "runner_root"
                / "usr"
                / "local"
                / "lib"
                / "android"
                / "sdk"
                / "ndk"
                / "27.3.13750724"
            )
            toolchain = _make_consumer_ndk(consumer_ndk, "linux-x86_64")

            baked_cc = f"{_BUILD_NDK_312}/bin/aarch64-linux-android24-clang"
            after = _render_and_exec(
                sd_path,
                build_prefix=_BUILD_PREFIX,
                build_ndk=_BUILD_NDK_312,
                baked_vars={"CC": baked_cc, "prefix": _BUILD_PREFIX},
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(tmp / "empty_home"),
                    "NDK_HOME": str(consumer_ndk),
                },
            )

            cc = after["CC"]
            expected = f"{toolchain}/bin/aarch64-linux-android24-clang"

            self.assertEqual(
                cc,
                expected,
                msg=(
                    "CC must resolve to the consumer's NDK toolchain. If this "
                    "fails, check that `_install_prefixes` doesn't contain "
                    "'/usr/local' — that entry would mangle the path the NDK "
                    "rule just substituted."
                ),
            )
            self.assertIn(
                "/usr/local/",
                cc,
                msg="CC must retain the /usr/local component of the consumer NDK.",
            )
            self.assertNotIn(
                str(consumer_prefix),
                cc,
                msg=(
                    "CC must NOT contain the consumer's install prefix — if it "
                    "does, an install-prefix substitution has mangled a path "
                    "inside the consumer's NDK."
                ),
            )

    def test_macos_dev_ndk_under_library(self):
        """3.13+ on a macOS dev box. Build-time NDK lives under /usr/local
        (Linux CI runner), consumer NDK lives at ~/Library/Android/sdk/ndk/.
        CC should resolve to the darwin-x86_64 toolchain with no /usr/local
        component left.
        """
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str).resolve()
            sd_path = _make_consumer_install(tmp)

            fake_home = tmp / "fake_home"
            macos_ndk = (
                fake_home / "Library" / "Android" / "sdk" / "ndk" / "27.3.13750724"
            )
            toolchain = _make_consumer_ndk(macos_ndk, "darwin-x86_64")

            baked_cc = f"{_BUILD_NDK_313}/bin/aarch64-linux-android24-clang"
            after = _render_and_exec(
                sd_path,
                build_prefix=_BUILD_PREFIX,
                build_ndk=_BUILD_NDK_313,
                baked_vars={"CC": baked_cc, "prefix": _BUILD_PREFIX},
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(fake_home),
                    # NDK_HOME deliberately unset — fallback walker must
                    # find ~/Library/Android/sdk/ndk/<ver>.
                },
            )

            cc = after["CC"]
            self.assertEqual(
                cc,
                f"{toolchain}/bin/aarch64-linux-android24-clang",
                msg="CC should resolve to the macOS darwin toolchain.",
            )
            self.assertNotIn(
                "/usr/local/",
                cc,
                msg="CC must not retain the build-time /usr/local NDK prefix.",
            )
            self.assertIn(
                "darwin-x86_64",
                cc,
                msg=(
                    "CC must use the consumer's darwin host triple, not the "
                    "build-time linux-x86_64."
                ),
            )

    def test_no_consumer_ndk_keeps_build_path(self):
        """If the relocator can't find any consumer NDK, it must leave CC
        at the build-time path — not corrupt or blank it.
        """
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str).resolve()
            sd_path = _make_consumer_install(tmp)

            baked_cc = f"{_BUILD_NDK_312}/bin/aarch64-linux-android24-clang"
            after = _render_and_exec(
                sd_path,
                build_prefix=_BUILD_PREFIX,
                build_ndk=_BUILD_NDK_312,
                baked_vars={"CC": baked_cc, "prefix": _BUILD_PREFIX},
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(tmp / "empty_home"),
                    # NDK_HOME deliberately unset, and no fallback dirs exist.
                },
            )

            self.assertEqual(after["CC"], baked_cc)

    def test_build_prefix_substitution(self):
        """A baked value referencing `_build_prefix` must be re-anchored
        to the consumer's install prefix.
        """
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str).resolve()
            sd_path = _make_consumer_install(tmp)
            consumer_prefix = sd_path.parents[2]

            after = _render_and_exec(
                sd_path,
                build_prefix=_BUILD_PREFIX,
                build_ndk=_BUILD_NDK_312,
                baked_vars={"LIBDIR": f"{_BUILD_PREFIX}/lib"},
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(tmp / "empty_home"),
                },
            )

            self.assertEqual(after["LIBDIR"], f"{consumer_prefix}/lib")

    def test_compound_value_both_substitutions_apply_cleanly(self):
        """A single baked value containing BOTH the build-time NDK and the
        build-time install prefix must have both rewritten — without
        either rule mangling the other's output. Guards against future
        order-of-substitution regressions.
        """
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str).resolve()
            sd_path = _make_consumer_install(tmp)
            consumer_prefix = sd_path.parents[2]

            consumer_ndk = tmp / "consumer_ndk"
            toolchain = _make_consumer_ndk(consumer_ndk, "linux-x86_64")

            baked_ldshared = (
                f"{_BUILD_NDK_312}/bin/aarch64-linux-android24-clang "
                f"-shared -L{_BUILD_PREFIX}/lib"
            )
            after = _render_and_exec(
                sd_path,
                build_prefix=_BUILD_PREFIX,
                build_ndk=_BUILD_NDK_312,
                baked_vars={"LDSHARED": baked_ldshared},
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(tmp / "empty_home"),
                    "NDK_HOME": str(consumer_ndk),
                },
            )

            ldshared = after["LDSHARED"]
            self.assertIn(
                f"{toolchain}/bin/aarch64-linux-android24-clang",
                ldshared,
                msg="NDK rule must rewrite the clang path.",
            )
            self.assertIn(
                f"-L{consumer_prefix}/lib",
                ldshared,
                msg="install-prefix rule must rewrite the -L path.",
            )
            self.assertNotIn(
                _BUILD_NDK_312, ldshared, msg="No build-time NDK path should remain."
            )
            self.assertNotIn(
                _BUILD_PREFIX,
                ldshared,
                msg="No build-time install prefix should remain.",
            )


if __name__ == "__main__":
    unittest.main()
