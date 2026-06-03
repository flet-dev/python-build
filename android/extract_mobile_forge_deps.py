#!/usr/bin/env python3
"""Extract bundled native libs from CPython 3.13+'s Android install into the
sibling per-lib layout that mobile-forge ``make_dep_wheels.py`` expects.

CPython's official Android tooling (used by python-build for 3.13+) builds
OpenSSL, bzip2, libffi, xz/lzma, and SQLite as part of the host build, but
installs the headers/libs intermixed inside the Python install. mobile-forge
expects each dep as a sibling ``install/android/<abi>/<lib>-<ver>-<N>/``
directory with its own ``include/`` and ``lib/`` so it can repackage each as
an ``<lib>-<ver>-<N>-py3-none-android_*.whl``.

This script reorganizes after the fact: for each known dep, detect its version
from headers/pkgconfig, materialize the sibling directory, copy the right
files, and append a matching line to ``support/<X.Y>/android/VERSIONS``.

Idempotent: re-running on an already-organized tree replaces the sibling
directories in place and de-duplicates the VERSIONS entries.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Callable


# Default build number for python-build-emitted dep wheels for 3.13+.
# Starts fresh at 1, independent of the 3.12 counter (where openssl had reached
# `-4`). Bump per-dep in the future if a rebuild changes the contents.
DEFAULT_BUILD_NUMBER = 1


def _version_from_header(prefix: Path, header: str, pattern: str) -> str | None:
    path = prefix / header
    if not path.is_file():
        return None
    match = re.search(pattern, path.read_text())
    return match.group(1) if match else None


def _openssl_version(prefix: Path) -> str | None:
    return _version_from_header(
        prefix, "include/openssl/opensslv.h",
        r'OPENSSL_VERSION_STR\s+"([0-9]+\.[0-9]+\.[0-9]+)"',
    )


def _libffi_version(prefix: Path) -> str | None:
    pc = prefix / "lib/pkgconfig/libffi.pc"
    if not pc.is_file():
        return None
    match = re.search(r"^Version:\s*([0-9.]+)", pc.read_text(), re.MULTILINE)
    return match.group(1) if match else None


def _lzma_version(prefix: Path) -> str | None:
    header = prefix / "include/lzma/version.h"
    if not header.is_file():
        return None
    text = header.read_text()
    parts = []
    for component in ("MAJOR", "MINOR", "PATCH"):
        m = re.search(rf"^#define\s+LZMA_VERSION_{component}\s+(\d+)", text, re.MULTILINE)
        if not m:
            return None
        parts.append(m.group(1))
    return ".".join(parts)


def _sqlite_version(prefix: Path) -> str | None:
    return _version_from_header(
        prefix, "include/sqlite3.h",
        r'SQLITE_VERSION\s+"([0-9.]+)"',
    )


def _bzip2_version(prefix: Path) -> str | None:
    # bzlib.h carries no version macro. CPython 3.13/3.14's official Android
    # tooling pins bzip2 1.0.8 (unchanged upstream since 2019). Return the
    # pinned version if the lib is present.
    if (prefix / "lib/libbz2.a").is_file() and (prefix / "include/bzlib.h").is_file():
        return "1.0.8"
    return None


# (lib_name, version_resolver, [(src_rel_to_prefix, dst_rel_to_dep_dir), ...]).
# Missing sources are silently skipped — that's how a tree built without one of
# the optional deps still produces an internally consistent VERSIONS file.
DEPS: list[tuple[str, Callable[[Path], str | None], list[tuple[str, str]]]] = [
    (
        "openssl",
        _openssl_version,
        [
            ("include/openssl", "include/openssl"),
            ("lib/libcrypto.a", "lib/libcrypto.a"),
            ("lib/libssl.a", "lib/libssl.a"),
        ],
    ),
    (
        "bzip2",
        _bzip2_version,
        [
            ("include/bzlib.h", "include/bzlib.h"),
            ("lib/libbz2.a", "lib/libbz2.a"),
        ],
    ),
    (
        "libffi",
        _libffi_version,
        [
            ("include/ffi.h", "include/ffi.h"),
            ("include/ffitarget.h", "include/ffitarget.h"),
            ("lib/libffi.a", "lib/libffi.a"),
            ("lib/pkgconfig/libffi.pc", "lib/pkgconfig/libffi.pc"),
        ],
    ),
    (
        "xz",
        _lzma_version,
        [
            ("include/lzma.h", "include/lzma.h"),
            ("include/lzma", "include/lzma"),
            ("lib/liblzma.a", "lib/liblzma.a"),
        ],
    ),
    (
        "sqlite",
        _sqlite_version,
        [
            ("include/sqlite3.h", "include/sqlite3.h"),
            ("include/sqlite3ext.h", "include/sqlite3ext.h"),
            ("lib/libsqlite3.so", "lib/libsqlite3.so"),
        ],
    ),
]


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def extract(prefix: Path, abi: str, version_short: str, support_versions: Path,
            build_number: int = DEFAULT_BUILD_NUMBER) -> list[str]:
    sibling_root = prefix.parent
    written: list[str] = []

    for lib_name, version_resolver, files in DEPS:
        version = version_resolver(prefix)
        if not version:
            continue

        dep_dir = sibling_root / f"{lib_name}-{version}-{build_number}"
        if dep_dir.exists():
            shutil.rmtree(dep_dir)

        any_copied = False
        for src_rel, dst_rel in files:
            src = prefix / src_rel
            if not src.exists():
                continue
            _copy(src, dep_dir / dst_rel)
            any_copied = True

        if not any_copied:
            continue

        entry = f"{lib_name}: {version}-{build_number}"
        written.append(entry)
        print(f"  wrote {dep_dir.relative_to(sibling_root.parent.parent)} ({entry})")

    _update_versions_file(support_versions, written)
    return written


def _update_versions_file(path: Path, new_entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.is_file() else ""

    keep: list[str] = []
    seen_keys: set[str] = set()
    for line in existing.splitlines():
        stripped = line.strip()
        if not stripped:
            keep.append(line)
            continue
        key = stripped.split(":", 1)[0].strip().lower()
        if any(key == entry.split(":", 1)[0].strip().lower() for entry in new_entries):
            # Skip any pre-existing line for a dep we're about to re-emit.
            continue
        keep.append(line)
        seen_keys.add(key)

    # Ensure the file ends with a newline before appending.
    body = "\n".join(keep)
    if body and not body.endswith("\n"):
        body += "\n"

    for entry in new_entries:
        body += entry + "\n"

    path.write_text(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path,
                        help="Path to the Python install root, e.g. "
                             "install/android/<abi>/python-<X.Y.Z>")
    parser.add_argument("--abi", required=True,
                        help="Android ABI (arm64-v8a, x86_64, etc.) — used in log output.")
    parser.add_argument("--version-short", required=True,
                        help="Python short version, e.g. 3.13 — used to locate the "
                             "support/<X.Y>/android/VERSIONS file.")
    parser.add_argument("--support-root", type=Path,
                        help="Root that contains support/<X.Y>/android/VERSIONS. "
                             "Defaults to two levels above the prefix's grandparent "
                             "(i.e. python-build/android).")
    parser.add_argument("--build-number", type=int, default=DEFAULT_BUILD_NUMBER,
                        help=f"Build number suffix (default: {DEFAULT_BUILD_NUMBER}).")
    args = parser.parse_args()

    prefix = args.prefix.resolve()
    if not prefix.is_dir():
        print(f"error: prefix is not a directory: {prefix}", file=sys.stderr)
        return 2

    # prefix = .../android/install/android/<abi>/python-<ver>
    # support root = .../android (3 levels up from <abi>)
    support_root = args.support_root.resolve() if args.support_root else prefix.parents[3]
    support_versions = support_root / "support" / args.version_short / "android" / "VERSIONS"

    print(f"extracting bundled native deps from {prefix.name} ({args.abi}) ...")
    written = extract(prefix, args.abi, args.version_short, support_versions,
                      build_number=args.build_number)
    if not written:
        print("  no deps found")
    print(f"  VERSIONS file: {support_versions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
