#!/usr/bin/env python3
"""Post-`make install` normalization for python-build's Android install tree.

Runs once on python-build's CI right after CPython's `make install` and
before the install tree is tarred up for downstream consumers
(mobile-forge, serious_python). Does two things:

  - Rewrites every `_sysconfigdata__*.py` under `prefix/lib/python*/`
    to self-relocate at import time. The shipped sysconfigdata has
    hard-coded python-build CI paths (`/home/runner/work/...`,
    `/home/runner/ndk/...`); the injected
    `_mobile_forge_relocate_sysconfig` function rewrites them on the
    fly to the consumer's actual on-disk layout. See
    `append_relocation_block` for the substitution model.

  - Replaces `prefix/lib/libpython3.so` (a stub for `-lpython3` abi3
    consumers) with a GNU ld linker script so consumer link commands
    record the correct DT_NEEDED. See `replace_libpython_stub`.

Invoked from `android/build.sh` at the end of the per-version build;
the contract point between python-build (what we ship) and
mobile-forge / serious_python (how they consume it).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def find_sysconfigdata(prefix: Path) -> list[Path]:
    """Locate every `_sysconfigdata__*.py` under a Python install tree.

    CPython names sysconfigdata files by host triple (e.g.
    `_sysconfigdata__linux_x86_64-linux-gnu.py`, `_sysconfigdata__linux_.py`),
    so the trailing identifier varies per build configuration. We glob to cover all
    of them and sort for a deterministic processing order.

    Args:
        prefix: A Python install prefix (the dir containing `lib/`).

    Returns:
        Sorted list of sysconfigdata file paths; may be empty.
    """
    return sorted((prefix / "lib").glob("python*/_sysconfigdata__*.py"))


def replace_libpython_stub(prefix: Path) -> None:
    """Replace `<prefix>/lib/libpython3.so` with a GNU ld linker script.

    abi3-stable extension wheels link against `-lpython3`. The linker
    resolves that to `libpython3.so` in the sysroot. On a vanilla
    install `libpython3.so` is a symlink to `libpython3.<version>.so`,
    and some linker pipelines record the symlink filename
    (`libpython3.so`) into the wheel's `DT_NEEDED` instead of the
    target's `SONAME`. When the wheel then ships to a device that only
    carries `libpython3.<version>.so`, `dlopen` fails to resolve
    `libpython3.so` and crashes.

    A linker script `INPUT ( -lpython3.<version> )` makes the linker
    resolve straight through to the versioned library at link time
    without going via a filename that could leak into `DT_NEEDED`.

    Idempotent: if `libpython3.so` is already a symlink pointing at the
    correct versioned target, leave it alone (consumers that work
    against that symlink keep working); otherwise replace it.

    Args:
        prefix: A Python install prefix (the dir containing `lib/`).
    """
    lib_dir = prefix / "lib"
    libpython = lib_dir / "libpython3.so"
    # `libpython3.X.so` is the canonical versioned name; pick the first
    # match (typically there's only one — the install-tree libpython).
    versioned = sorted(lib_dir.glob("libpython3.[0-9]*.so"))
    if not libpython.exists() or not versioned:
        # Nothing to retarget. Tree was built without a libpython3.so
        # stub, or the versioned library never landed — in either case
        # there's no consumer-visible breakage to fix.
        return

    target = versioned[0].name
    # Already a symlink pointing at the right versioned target — leave
    # it alone so consumers that work against that symlink keep working.
    if libpython.is_symlink() and os.readlink(libpython) == target:
        return

    # Replace whatever's at libpython3.so (stale symlink, regular file from a
    # previous run, …) with a one-line ld linker script. `INPUT ( -lpython3.X )`
    # tells ld to resolve through to libpython3.X.so without recording the bare
    # `libpython3.so` name into the consumer's `DT_NEEDED`.
    libpython.unlink()
    libpython.write_text(
        f"INPUT ( -l{target.removeprefix('lib').removesuffix('.so')} )\n"
    )


def append_relocation_block(
    path: Path, prefix: Path, ndk_toolchain: str | None
) -> None:
    """Append a self-relocating block to a `_sysconfigdata__*.py` file.

    The appended block defines `_mobile_forge_relocate_sysconfig` and
    calls it immediately, so any code that imports the sysconfigdata
    module (CPython's `sysconfig` machinery, mobile-forge's crossenv,
    setuptools/meson cross builds, etc.) sees `build_time_vars` already rewritten
    for the consumer's filesystem — no explicit "please relocate" call required.

    Two substitution rules apply:

      1. `_install_prefixes` → `_prefix`. Re-anchors python-build CI's
         `$PREFIX` (`/home/runner/work/.../install/...`) to wherever
         the consumer has the install tree on disk, derived from the
         sysconfigdata file's own `__file__` via `parents[2]`.

      2. `_build_ndk` → `_local_ndk`. Re-anchors python-build CI's NDK
         toolchain path (e.g. `/home/runner/ndk/r27d/.../linux-x86_64`)
         to whichever NDK the consumer can find locally — looked up
         via `NDK_HOME`/`ANDROID_NDK_HOME`, then `~/ndk/<ver>`, then
         the standard SDK roots.

    The two rules operate on disjoint substrings — no `_install_prefixes`
    entry ever overlaps the NDK toolchain path — so the order between
    them is irrelevant in correctness terms.

    Idempotent: the block carries a marker comment; re-applying it is a no-op.

    Args:
        path: The `_sysconfigdata__*.py` file to mutate in place.
        prefix: python-build CI's `$PREFIX`, baked verbatim into the rendered
            block as `_build_prefix`.
        ndk_toolchain: python-build CI's NDK toolchain path, baked verbatim
            into the rendered block as `_build_ndk`. May be `None` on
            non-Android sysconfigdata (the NDK substitution rule then no-ops).
    """
    marker = "# mobile-forge sysconfig relocation"
    text = path.read_text()
    if marker in text:
        # Already applied (e.g. re-running build.sh after a partial failure).
        return

    block = f"""

{marker}
def _mobile_forge_relocate_sysconfig():
    # Runs once at sysconfigdata import time on the consumer host. Rewrites every path
    # string baked into `build_time_vars` (CC, LDSHARED, LIBDIR, etc.) from python-build
    # CI's filesystem layout to the consumer's.
    import os as _os
    from pathlib import Path as _Path

    # __file__ = <install_prefix>/lib/python<X.Y>/_sysconfigdata__*.py
    # parents[2] = <install_prefix> — what the consumer needs us to re-anchor build-time paths at.
    _prefix = str(_Path(__file__).resolve().parents[2])
    _build_prefix = {str(prefix)!r}
    _install_prefixes = (_build_prefix,)
    _build_ndk = {ndk_toolchain!r}

    def _candidate_ndk_homes():
        _seen = set()
        _build_ndk_version = None
        if _build_ndk:
            _parts = _Path(_build_ndk).parts
            if "toolchains" in _parts:
                _build_ndk_version = _parts[_parts.index("toolchains") - 1]

        def _emit(_path):
            if _path and _path not in _seen and _path.is_dir():
                _seen.add(_path)
                return _path
            return None

        # 1. Explicit env overrides — highest priority.
        for _value in (_os.environ.get("NDK_HOME"), _os.environ.get("ANDROID_NDK_HOME")):
            _path = _emit(_Path(_value)) if _value else None
            if _path:
                yield _path

        # 2. Legacy `~/ndk/<build-time-letter>/` layout (e.g. ~/ndk/r27d/) —
        # what older install scripts (incl. mobile-forge's pre-sdkmanager
        # install_ndk.sh) used. Looked up by the build-time letter form
        # baked into the toolchain path, so only fires when present.
        _home = _Path.home()
        if _build_ndk_version:
            _legacy = _home / "ndk" / _build_ndk_version
            _path = _emit(_legacy)
            if _path:
                yield _path

        # 3. Fallback — walk every known NDK root and yield each child,
        # newest first. Any modern NDK can serve as a substitute (clang
        # is forward-compatible at the API levels mobile-forge targets),
        # so this is robust to letter/component-version drift without
        # needing a hardcoded translation table.
        for _root in (
            _home / "ndk",
            _home / "Library" / "Android" / "sdk" / "ndk",
            _home / "Android" / "Sdk" / "ndk",
        ):
            if not _root.is_dir():
                continue
            for _child in sorted(_root.iterdir(), reverse=True):
                _path = _emit(_child)
                if _path:
                    yield _path

    def _local_toolchain():
        if not _build_ndk:
            return None
        for _ndk_home in _candidate_ndk_homes():
            _prebuilt = _ndk_home / "toolchains" / "llvm" / "prebuilt"
            if not _prebuilt.is_dir():
                continue
            for _toolchain in sorted(_prebuilt.iterdir()):
                if (_toolchain / "bin").is_dir():
                    return str(_toolchain)
        return None

    _local_ndk = _local_toolchain()

    # Apply substitution rules
    for _key, _value in tuple(build_time_vars.items()):
        if not isinstance(_value, str):
            continue
        # Rule 1 (install-prefix): re-anchor python-build $PREFIX to the consumer's install location.
        for _old_prefix in _install_prefixes:
            _value = _value.replace(_old_prefix, _prefix)
        # Rule 2 (NDK): re-anchor the build-time toolchain to whichever NDK the consumer has locally.
        if _build_ndk and _local_ndk:
            _value = _value.replace(_build_ndk, _local_ndk)
        build_time_vars[_key] = _value


_mobile_forge_relocate_sysconfig()
del _mobile_forge_relocate_sysconfig
"""
    path.write_text(text + block)


def rewrite_build_details_json(prefix: Path) -> None:
    """Re-anchor absolute paths in ``build-details.json`` (Python 3.14+).

    CPython's official Android tooling (used on the 3.13+ build path) emits
    ``lib/python<X.Y>/build-details.json`` alongside the per-version
    sysconfigdata. Consumers like ``maturin`` read this JSON for
    cross-compilation — most notably the ``libpython.dynamic`` /
    ``libpython.dynamic_stableabi`` paths, which become the ``-L`` argument
    to the consumer's linker. Every absolute path in the file points at
    python-build CI's build-time install root (currently ``/usr/local``,
    but read from the JSON so we don't bake an assumption); on every
    consumer machine that path is empty, so the linker fails with
    ``unable to find library -lpython3`` and the build dies.

    Re-anchor each absolute path field by replacing the build-time prefix
    (read from ``base_prefix`` in the JSON itself) with the on-disk install
    prefix. Reading the substitution source out of the file rather than
    hard-coding ``/usr/local`` matches the narrow-prefix discipline the
    sysconfig relocator follows: an upstream CPython change that moves the
    Android tooling's install root won't silently miss the rewrite, and
    nothing else under the install prefix's namespace can be accidentally
    rewritten (e.g. a future ``c_api.headers`` value that happens to live
    under a path sharing a prefix substring with an unrelated tree).

    Idempotent: once ``base_prefix`` has been re-anchored at ``str(prefix)``,
    the build-time pattern no longer appears in the JSON and re-running the
    substitution is a no-op.
    """
    candidates = sorted(prefix.glob("lib/python*/build-details.json"))
    if not candidates:
        return
    prefix_str = str(prefix)
    for path in candidates:
        data = json.loads(path.read_text())
        build_time_prefix = data.get("base_prefix")
        if not build_time_prefix or build_time_prefix == prefix_str:
            # Either the file has no `base_prefix` to anchor on (unexpected
            # for a CPython-emitted build-details.json) or we already
            # rewrote this file in a prior pass — either way, nothing to do.
            continue
        text = path.read_text()
        new_text = text.replace(build_time_prefix, prefix_str)
        if new_text != text:
            path.write_text(new_text)


def main() -> None:
    """CLI entry point: normalize one install prefix end-to-end.

    Walks every sysconfigdata under `prefix/lib/python*/` and appends the
    self-relocation block (idempotent), then retargets `libpython3.so` to a linker
    script. Invoked from `android/build.sh` once per per-version build.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "prefix",
        type=Path,
        help="Install prefix to normalize (directory containing lib/).",
    )
    parser.add_argument(
        "--ndk-toolchain",
        help="Build-time NDK toolchain path (for example: "
        "~/ndk/r27d/toolchains/llvm/prebuilt/linux-x86_64). Baked into every "
        "sysconfigdata's relocation block so the consumer-side substitution "
        "knows what to replace. Omit on non-Android trees.",
    )
    args = parser.parse_args()

    prefix = args.prefix.resolve()
    for sysconfigdata in find_sysconfigdata(prefix):
        append_relocation_block(sysconfigdata, prefix, args.ndk_toolchain)
    rewrite_build_details_json(prefix)
    replace_libpython_stub(prefix)


if __name__ == "__main__":
    main()
