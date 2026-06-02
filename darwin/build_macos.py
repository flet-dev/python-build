#!/usr/bin/env python3
"""Build a macOS universal2 ``Python.framework`` from source and wrap it in an XCframework.

This replaces beeware's Make-based macOS path (which re-bundled python.org's official
``.pkg``) for *all* versions (3.12 / 3.13 / 3.14). Building from source means we get the
exact micro version even when python.org never published a macOS installer for it.

macOS provides bz2/lzma/sqlite/libffi via the SDK and CPython bundles libmpdec, so the
only third-party dependency we must supply is **OpenSSL** — and since no prebuilt
universal2 macOS OpenSSL exists (cpython-apple-source-deps only ships iOS-family slices,
Homebrew is single-arch), we build it universal2 from source here.

Output (normalized layout consumed by ``package-macos-for-dart.sh``):
    <root>/install/macOS/macosx/python-<ver>/Python.framework      # installed framework
    <root>/support/<short>/macOS/Python.xcframework                # framework wrapped as xcframework

Run from the ``darwin/`` directory:
    python build_macos.py 3.13.13
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

_LOG = None  # open file handle; set via set_log()

# Third-party libs built from source. macOS ships bz2/sqlite/zlib/libffi *headers* in the
# SDK, but NOT OpenSSL (none at all) nor liblzma (dylib only, no lzma.h), so we build those
# two universal2 from source. Pinned for reproducibility, overridable via CLI.
DEFAULT_OPENSSL_VERSION = "3.6.2"
DEFAULT_XZ_VERSION = "5.8.3"
# zstd backs the _zstd module added in CPython 3.14 (PEP 784); built only for 3.14+.
DEFAULT_ZSTD_VERSION = "1.5.7"
DEFAULT_DEPLOYMENT_TARGET = "11.0"
ARCHES = ("x86_64", "arm64")

# A minimal PATH keeps Homebrew / user Python installs from leaking into the build.
CLEAN_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/Library/Apple/usr/bin"


def set_log(path: Path) -> None:
    """Tee all subsequent run()/_emit output to a build log file."""
    global _LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOG = open(path, "w", buffering=1)
    _emit(f">>> Build log: {path}")


def _emit(line: str) -> None:
    sys.stdout.write(line if line.endswith("\n") else line + "\n")
    sys.stdout.flush()
    if _LOG:
        _LOG.write(line if line.endswith("\n") else line + "\n")


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    printable = " ".join(str(c) for c in cmd)
    _emit(f">>> {printable}" + (f"  (cwd={cwd})" if cwd else ""))
    # Merge stderr into stdout and tee line-by-line to console + log file.
    proc = subprocess.Popen(
        [str(c) for c in cmd], cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _emit(line.rstrip("\n"))
    if proc.wait() != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def download(url: str, dest: Path) -> None:
    if dest.exists():
        print(f">>> Using cached {dest.name}")
        return
    print(f">>> Download {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    tmp.replace(dest)


def base_env(deployment_target: str) -> dict:
    env = dict(os.environ)
    env["PATH"] = CLEAN_PATH
    env["MACOSX_DEPLOYMENT_TARGET"] = deployment_target
    return env


# ---------------------------------------------------------------------------
# OpenSSL (universal2, built from source)
# ---------------------------------------------------------------------------

def build_openssl_universal(
    openssl_version: str,
    downloads: Path,
    build_dir: Path,
    deployment_target: str,
    jobs: int,
) -> Path:
    """Build OpenSSL for each arch as a SHARED lib, fuse into a universal2 install.

    Returns the prefix containing ``include/`` and ``lib/{libssl,libcrypto}.3.dylib``.
    The dylib install-names are left as the (absolute) build prefix here; they are
    rewritten to @rpath when bundled into the framework (see relocate_openssl). All paths
    are keyed by the OpenSSL version so switching versions never reuses stale artifacts.
    """
    out_prefix = build_dir / f"openssl-{openssl_version}-universal"
    if (out_prefix / "lib" / "libssl.3.dylib").exists():
        _emit(f">>> Using cached universal OpenSSL at {out_prefix}")
        return out_prefix

    tarball = downloads / f"openssl-{openssl_version}.tar.gz"
    download(
        f"https://github.com/openssl/openssl/releases/download/"
        f"openssl-{openssl_version}/openssl-{openssl_version}.tar.gz",
        tarball,
    )

    env = base_env(deployment_target)
    # Configure every arch with the SAME --prefix so the baked install-names match,
    # then redirect the actual install per arch with DESTDIR. That keeps the fat
    # dylibs' load commands consistent so install_name_tool can rewrite them once.
    arch_includes: dict[str, Path] = {}
    arch_libdirs: dict[str, Path] = {}
    for arch in ARCHES:
        src = build_dir / f"openssl-{openssl_version}-{arch}"
        dest = build_dir / f"openssl-{openssl_version}-dest-{arch}"
        if src.exists():
            shutil.rmtree(src)
        if dest.exists():
            shutil.rmtree(dest)
        src.mkdir(parents=True)
        run(["tar", "xzf", tarball, "--strip-components", "1", "-C", src])
        # Default (no "no-shared") builds the shared dylibs; darwin64-<arch>-cc sets -arch.
        run(
            [
                "./Configure",
                f"darwin64-{arch}-cc",
                "no-tests",
                f"--prefix={out_prefix}",
                f"--openssldir={out_prefix}/ssl",
                f"-mmacosx-version-min={deployment_target}",
            ],
            cwd=src,
            env=env,
        )
        run(["make", f"-j{jobs}"], cwd=src, env=env)
        run(["make", "install_sw", f"DESTDIR={dest}"], cwd=src, env=env)
        # DESTDIR + absolute prefix -> files land at <dest><out_prefix>.
        inner = Path(f"{dest}{out_prefix}")
        arch_includes[arch] = inner / "include"
        arch_libdirs[arch] = inner / "lib"

    if out_prefix.exists():
        shutil.rmtree(out_prefix)
    (out_prefix / "lib").mkdir(parents=True)
    for lib in ("libssl.3.dylib", "libcrypto.3.dylib"):
        run(
            ["lipo", "-create", "-output", out_prefix / "lib" / lib]
            + [arch_libdirs[a] / lib for a in ARCHES]
        )
    # Unversioned symlinks so `-lssl`/`-lcrypto` resolve at link time.
    (out_prefix / "lib" / "libssl.dylib").symlink_to("libssl.3.dylib")
    (out_prefix / "lib" / "libcrypto.dylib").symlink_to("libcrypto.3.dylib")
    # Base the include tree on one arch, then dispatch the arch-dependent headers.
    shutil.copytree(arch_includes[ARCHES[0]], out_prefix / "include")
    _dispatch_openssl_headers({a: arch_includes[a].parent for a in ARCHES},
                              out_prefix / "include")
    return out_prefix


def _dispatch_openssl_headers(arch_prefixes: dict[str, Path], include_dir: Path) -> None:
    """Replace arch-dependent OpenSSL headers with a per-arch #include dispatcher.

    ``opensslconf.h`` / ``configuration.h`` are generated per arch; in a universal2
    compile both arches are processed in one pass, so a single copy would be wrong for
    one of them. We keep both arch variants and pick at preprocessor time.
    """
    for header in ("opensslconf.h", "configuration.h"):
        rel = Path("openssl") / header
        variants: dict[str, Path] = {}
        for arch in ARCHES:
            src = arch_prefixes[arch] / "include" / rel
            if src.exists():
                variants[arch] = src
        if not variants:
            continue
        for arch, src in variants.items():
            shutil.copyfile(src, include_dir / "openssl" / f"{header[:-2]}-{arch}.h")
        guard = {
            "x86_64": "defined(__x86_64__)",
            "arm64": "defined(__arm64__) || defined(__aarch64__)",
        }
        lines = ["/* Auto-generated universal2 dispatcher (python-build) */"]
        first = True
        for arch in ARCHES:
            if arch not in variants:
                continue
            lines.append(f"#{'if' if first else 'elif'} {guard[arch]}")
            lines.append(f'#  include "{header[:-2]}-{arch}.h"')
            first = False
        lines.append("#else")
        lines.append('#  error "Unsupported architecture for universal2 OpenSSL headers"')
        lines.append("#endif")
        (include_dir / "openssl" / header).write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# liblzma / xz (universal2, built from source — macOS ships no lzma.h)
# ---------------------------------------------------------------------------

def build_xz_universal(
    xz_version: str,
    downloads: Path,
    build_dir: Path,
    deployment_target: str,
    jobs: int,
) -> Path:
    """Build a universal2 static liblzma. Returns a prefix with include/ + lib/liblzma.a.

    Unlike OpenSSL, xz's autotools build can emit a fat binary in a single pass (its
    headers are arch-independent), so we compile with both -arch flags rather than
    per-arch + lipo.
    """
    out_prefix = build_dir / f"xz-{xz_version}-universal"
    if (out_prefix / "lib" / "liblzma.a").exists():
        _emit(f">>> Using cached universal liblzma at {out_prefix}")
        return out_prefix

    tarball = downloads / f"xz-{xz_version}.tar.gz"
    download(
        f"https://github.com/tukaani-project/xz/releases/download/"
        f"v{xz_version}/xz-{xz_version}.tar.gz",
        tarball,
    )
    src = build_dir / f"xz-{xz_version}"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    run(["tar", "xzf", tarball, "--strip-components", "1", "-C", src])

    env = base_env(deployment_target)
    env["CFLAGS"] = f"-arch arm64 -arch x86_64 -mmacosx-version-min={deployment_target}"
    if out_prefix.exists():
        shutil.rmtree(out_prefix)
    run(
        [
            "./configure",
            "--disable-shared", "--enable-static",
            "--disable-doc", "--disable-nls", "--disable-scripts",
            # We only need liblzma, not the CLI tools.
            "--disable-xz", "--disable-xzdec", "--disable-lzmadec",
            "--disable-lzmainfo", "--disable-lzma-links",
            f"--prefix={out_prefix}",
        ],
        cwd=src, env=env,
    )
    run(["make", f"-j{jobs}"], cwd=src, env=env)
    run(["make", "install"], cwd=src, env=env)
    return out_prefix


# ---------------------------------------------------------------------------
# libzstd (universal2 static — backs the _zstd module new in CPython 3.14)
# ---------------------------------------------------------------------------

def build_zstd_universal(
    zstd_version: str,
    downloads: Path,
    build_dir: Path,
    deployment_target: str,
    jobs: int,
) -> Path:
    """Build a universal2 static libzstd. Returns a prefix with include/ + lib/libzstd.a."""
    out_prefix = build_dir / f"zstd-{zstd_version}-universal"
    if (out_prefix / "lib" / "libzstd.a").exists():
        _emit(f">>> Using cached universal libzstd at {out_prefix}")
        return out_prefix

    tarball = downloads / f"zstd-{zstd_version}.tar.gz"
    download(
        f"https://github.com/facebook/zstd/releases/download/"
        f"v{zstd_version}/zstd-{zstd_version}.tar.gz",
        tarball,
    )
    src = build_dir / f"zstd-{zstd_version}"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    run(["tar", "xzf", tarball, "--strip-components", "1", "-C", src])

    env = base_env(deployment_target)
    env["CFLAGS"] = f"-arch arm64 -arch x86_64 -mmacosx-version-min={deployment_target}"
    # zstd's lib/ Makefile builds the static lib directly (single-pass universal).
    run(["make", "-C", "lib", "libzstd.a", f"-j{jobs}"], cwd=src, env=env)

    if out_prefix.exists():
        shutil.rmtree(out_prefix)
    (out_prefix / "lib").mkdir(parents=True)
    (out_prefix / "include").mkdir(parents=True)
    shutil.copyfile(src / "lib" / "libzstd.a", out_prefix / "lib" / "libzstd.a")
    for header in ("zstd.h", "zdict.h", "zstd_errors.h"):
        shutil.copyfile(src / "lib" / header, out_prefix / "include" / header)
    return out_prefix


# ---------------------------------------------------------------------------
# CPython framework build
# ---------------------------------------------------------------------------

def build_python_framework(
    version: str,
    short: str,
    downloads: Path,
    build_dir: Path,
    install_dir: Path,
    openssl_prefix: Path,
    xz_prefix: Path,
    zstd_prefix: Path | None,
    deployment_target: str,
    jobs: int,
) -> Path:
    """Configure + build + install a universal2 framework. Returns the Python.framework path."""
    tarball = downloads / f"Python-{version}.tgz"
    download(f"https://www.python.org/ftp/python/{version}/Python-{version}.tgz", tarball)

    src = build_dir / f"Python-{version}"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    run(["tar", "xzf", tarball, "--strip-components", "1", "-C", src])

    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True)

    env = base_env(deployment_target)
    configure = [
        "./configure",
        "--enable-framework=" + str(install_dir),
        "--enable-universalsdk=/",
        "--with-universal-archs=universal2",
        "--with-openssl=" + str(openssl_prefix),
        # liblzma has no pkg-config on macOS; feed the from-source build directly so
        # the _lzma extension builds (the SDK provides no lzma.h).
        f"LIBLZMA_CFLAGS=-I{xz_prefix}/include",
        f"LIBLZMA_LIBS=-L{xz_prefix}/lib -llzma",
    ]
    if zstd_prefix is not None:
        # _zstd (CPython 3.14+) — SDK provides no zstd.h, so feed the from-source build.
        configure += [
            f"LIBZSTD_CFLAGS=-I{zstd_prefix}/include",
            f"LIBZSTD_LIBS=-L{zstd_prefix}/lib -lzstd",
        ]
    # NOTE: --enable-optimizations (PGO+LTO) intentionally omitted for first bring-up —
    # it is slow and the profile task is finicky for universal2. Re-enable once green.
    configure.append("--without-ensurepip")
    run(configure, cwd=src, env=env)
    run(["make", f"-j{jobs}"], cwd=src, env=env)
    # `make install` on a --enable-framework build also runs sub-targets that write
    # OUTSIDE our prefix: the IDLE/Python Launcher .app bundles -> /Applications, and
    # console-script symlinks -> /usr/local/bin (which needs root and fails). Redirect
    # both to throwaway dirs under build/ so only the framework+stdlib land in our
    # prefix and nothing touches the system.
    junk_apps = build_dir / "_appsdir"
    junk_bin = build_dir / "_unixtools"
    run(
        ["make", "install",
         f"PYTHONAPPSDIR={junk_apps}",
         f"FRAMEWORKUNIXTOOLSPREFIX={junk_bin}"],
        cwd=src, env=env,
    )

    framework = install_dir / "Python.framework"
    if not framework.exists():
        raise SystemExit(f"Expected framework at {framework}, but it was not produced.")
    return framework


# ---------------------------------------------------------------------------
# Bundle OpenSSL + relocate + strip + codesign + xcframework
# ---------------------------------------------------------------------------

def relocate_openssl(framework: Path, short: str, openssl_prefix: Path) -> None:
    """Bundle the shared OpenSSL dylibs into the framework and point everything at
    @rpath, matching the official/beeware layout (libssl/libcrypto under Versions/<short>/lib).

    _ssl/_hashlib were linked against ``<openssl_prefix>/lib/lib*.3.dylib`` (absolute), so
    we copy those dylibs in, rewrite their ids + interdependency to @rpath, and rewrite the
    modules' references to match.
    """
    lib_dir = framework / "Versions" / short / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    rpath = f"@rpath/Python.framework/Versions/{short}/lib"

    names = ("libcrypto.3.dylib", "libssl.3.dylib")
    for name in names:
        shutil.copyfile(openssl_prefix / "lib" / name, lib_dir / name)
    for link, target in (("libcrypto.dylib", "libcrypto.3.dylib"),
                         ("libssl.dylib", "libssl.3.dylib")):
        dst = lib_dir / link
        if not dst.exists():
            dst.symlink_to(target)

    # Fix the bundled dylibs' own ids and libssl's dependency on libcrypto.
    for name in names:
        run(["install_name_tool", "-id", f"{rpath}/{name}", lib_dir / name])
    run(["install_name_tool", "-change",
         f"{openssl_prefix}/lib/libcrypto.3.dylib", f"{rpath}/libcrypto.3.dylib",
         lib_dir / "libssl.3.dylib"])

    # Rewrite every module that linked the build-prefix OpenSSL to the bundled @rpath one.
    for module in framework.rglob("*"):
        if module.suffix not in (".so", ".dylib") or not module.is_file() or module.is_symlink():
            continue
        try:
            otool = subprocess.run(
                ["otool", "-L", str(module)], capture_output=True, text=True, check=True
            ).stdout
        except subprocess.CalledProcessError:
            continue
        for name in names:
            old = f"{openssl_prefix}/lib/{name}"
            if old in otool:
                run(["install_name_tool", "-change", old, f"{rpath}/{name}", module])


def strip_framework(framework: Path, short: str) -> None:
    """Strip local/debug symbols from all binaries (keeps exported symbols). Matches the
    stripped binaries the official build ships; must run before codesigning."""
    targets = [framework / "Versions" / short / "Python"]
    for p in framework.rglob("*"):
        if p.suffix in (".so", ".dylib") and p.is_file() and not p.is_symlink():
            targets.append(p)
    # `strip -x` removes local symbols but keeps externals needed for dynamic linking.
    for t in targets:
        run(["strip", "-x", t])


def make_relocatable(framework: Path, short: str) -> None:
    """Rewrite absolute /Library/... install names to @rpath (ported from beeware's
    patch/make-relocatable.sh)."""
    versions = framework / "Versions" / short
    python_lib = versions / "Python"
    run(["install_name_tool", "-id",
         f"@rpath/Python.framework/Versions/{short}/Python", python_lib])

    lib_dir = versions / "lib"
    versioned_dylibs = list(lib_dir.glob("*.*.dylib")) if lib_dir.is_dir() else []
    for dylib in versioned_dylibs:
        if dylib.name == f"libpython{short}.dylib":
            continue
        run(["install_name_tool", "-id",
             f"@rpath/Python.framework/Versions/{short}/lib/{dylib.name}", dylib])

    old_prefix = f"/Library/Frameworks/Python.framework/Versions/{short}"
    for module in framework.rglob("*"):
        if module.suffix not in (".dylib", ".so") or not module.is_file():
            continue
        try:
            otool = subprocess.run(
                ["otool", "-L", str(module)], capture_output=True, text=True, check=True
            ).stdout
        except subprocess.CalledProcessError:
            continue
        for dylib in versioned_dylibs:
            old = f"{old_prefix}/lib/{dylib.name}"
            if old in otool:
                run(["install_name_tool", "-change", old,
                     f"@rpath/Python.framework/Versions/{short}/lib/{dylib.name}", module])
        if f"{old_prefix}/Python" in otool:
            run(["install_name_tool", "-change", f"{old_prefix}/Python",
                 f"@rpath/Python.framework/Versions/{short}/Python", module])


def codesign_framework(framework: Path, short: str) -> None:
    args = ["codesign", "-s", "-", "--preserve-metadata=identifier,entitlements,flags,runtime", "-f"]
    run(args + [framework / "Versions" / short / "Python"])
    for pattern in ("*.dylib", "*.so"):
        for binary in framework.rglob(pattern):
            if binary.is_file():
                run(args + [binary])
    run(args + [framework])


def create_xcframework(framework: Path, output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["xcodebuild", "-create-xcframework", "-framework", framework, "-output", output])


# ---------------------------------------------------------------------------

def write_versions_file(support_dir: Path, version: str, openssl_version: str,
                        xz_version: str, zstd_version: str | None,
                        deployment_target: str) -> None:
    support_dir.mkdir(parents=True, exist_ok=True)
    text = (
        f"Python version: {version}\n"
        f"Build: custom\n"
        f"Min macOS version: {deployment_target}\n"
        f"---------------------\n"
        f"OpenSSL: {openssl_version}\n"
        f"XZ: {xz_version}\n"
    )
    if zstd_version is not None:
        text += f"zstd: {zstd_version}\n"
    (support_dir / "VERSIONS").write_text(text)


def main() -> None:
    if platform.system() != "Darwin":
        raise SystemExit("build_macos.py must run on macOS.")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="full CPython version, e.g. 3.13.13")
    parser.add_argument("--openssl-version", default=DEFAULT_OPENSSL_VERSION)
    parser.add_argument("--xz-version", default=DEFAULT_XZ_VERSION)
    parser.add_argument("--zstd-version", default=DEFAULT_ZSTD_VERSION)
    parser.add_argument("--deployment-target", default=DEFAULT_DEPLOYMENT_TARGET)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--app-store-compliance", action="store_true",
                        help="apply macos_support/app-store-compliance.patch to the stdlib")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="output root (default: cwd; emits install/ and support/ here)")
    args = parser.parse_args()

    version = args.version
    parts = version.split(".")
    short = f"{parts[0]}.{parts[1]}"
    minor = int(parts[1])

    root = args.root.resolve()
    script_dir = Path(__file__).resolve().parent
    downloads = root / "downloads"
    build_dir = root / "build" / "macOS"
    install_dir = root / "install" / "macOS" / "macosx" / f"python-{version}"
    xcframework_out = root / "support" / short / "macOS" / "Python.xcframework"

    set_log(build_dir / f"build-macos-{version}.log")

    openssl_prefix = build_openssl_universal(
        args.openssl_version, downloads, build_dir,
        args.deployment_target, args.jobs,
    )
    xz_prefix = build_xz_universal(
        args.xz_version, downloads, build_dir,
        args.deployment_target, args.jobs,
    )
    # _zstd is new in CPython 3.14; older versions have no such module.
    zstd_prefix = None
    zstd_version = None
    if minor >= 14:
        zstd_version = args.zstd_version
        zstd_prefix = build_zstd_universal(
            zstd_version, downloads, build_dir,
            args.deployment_target, args.jobs,
        )

    framework = build_python_framework(
        version, short, downloads, build_dir, install_dir,
        openssl_prefix, xz_prefix, zstd_prefix, args.deployment_target, args.jobs,
    )

    if args.app_store_compliance:
        patch = script_dir / "macos_support" / "app-store-compliance.patch"
        stdlib = framework / "Versions" / short / "lib" / f"python{short}"
        run(["patch", "--strip", "2", "--directory", stdlib, "--input", patch])

    relocate_openssl(framework, short, openssl_prefix)
    make_relocatable(framework, short)
    strip_framework(framework, short)
    codesign_framework(framework, short)
    create_xcframework(framework, xcframework_out)
    write_versions_file(root / "support" / short / "macOS", version,
                        args.openssl_version, args.xz_version, zstd_version,
                        args.deployment_target)

    _emit(f"\n>>> macOS build complete for {version}")
    _emit(f"    framework:   {framework}")
    _emit(f"    xcframework: {xcframework_out}")


if __name__ == "__main__":
    main()
