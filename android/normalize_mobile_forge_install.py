#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path


def find_sysconfigdata(prefix: Path) -> list[Path]:
    return sorted((prefix / "lib").glob("python*/_sysconfigdata__*.py"))


def replace_libpython_stub(prefix: Path) -> None:
    lib_dir = prefix / "lib"
    libpython = lib_dir / "libpython3.so"
    versioned = sorted(lib_dir.glob("libpython3.[0-9]*.so"))
    if not libpython.exists() or not versioned:
        return

    target = versioned[0].name
    if libpython.is_symlink() and os.readlink(libpython) == target:
        return

    libpython.unlink()
    libpython.write_text(f"INPUT ( -l{target.removeprefix('lib').removesuffix('.so')} )\n")


def append_relocation_block(path: Path, prefix: Path, ndk_toolchain: str | None) -> None:
    marker = "# mobile-forge sysconfig relocation"
    text = path.read_text()
    if marker in text:
        return

    block = f"""

{marker}
def _mobile_forge_relocate_sysconfig():
    import os as _os
    from pathlib import Path as _Path

    _prefix = str(_Path(__file__).resolve().parents[2])
    _build_prefix = {str(prefix)!r}
    _install_prefixes = (_build_prefix, "/usr/local")
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
    for _key, _value in tuple(build_time_vars.items()):
        if not isinstance(_value, str):
            continue
        # NDK substitution must run before install-prefix substitution: when
        # the build-time NDK lives under one of `_install_prefixes` (e.g. the
        # GitHub runner places NDK under `/usr/local/lib/android/sdk/ndk/...`),
        # rewriting the prefix first would mangle the NDK string and leave
        # nothing for the NDK rule to match. Swapping order keeps both rules
        # independent: NDK fully resolves to the local toolchain, then any
        # remaining install-prefix references get re-anchored.
        if _build_ndk and _local_ndk:
            _value = _value.replace(_build_ndk, _local_ndk)
        for _old_prefix in _install_prefixes:
            _value = _value.replace(_old_prefix, _prefix)
        build_time_vars[_key] = _value


_mobile_forge_relocate_sysconfig()
del _mobile_forge_relocate_sysconfig
"""
    path.write_text(text + block)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", type=Path)
    parser.add_argument("--ndk-toolchain")
    args = parser.parse_args()

    prefix = args.prefix.resolve()
    for sysconfigdata in find_sysconfigdata(prefix):
        append_relocation_block(sysconfigdata, prefix, args.ndk_toolchain)
    replace_libpython_stub(prefix)


if __name__ == "__main__":
    main()
