#!/usr/bin/env python3
"""Build iOS ``Python.xcframework`` + per-arch installs using CPython's standard Apple tool.

Every supported version is built with the **same** mechanism — CPython's in-tree
``Apple`` build package (``python Apple build iOS``):

* **3.14+** — the ``Apple/`` tooling is native; build straight from the source tarball.
* **3.13 / 3.12** — the tooling (and, for 3.12, the PEP 730 iOS runtime) isn't upstream yet,
  so we first apply a vendored back-port patch (``ios_patches/<short>/Python.patch``) that
  adds it, then run the identical ``python Apple build iOS``. No dependency on beeware's
  Python-Apple-support repo — only the vendored patch (which we maintain).

The Apple tool downloads its own pre-compiled C deps (from cpython-apple-source-deps, the
URL CPython itself hard-codes), builds every slice, and produces the xcframework. We then
reshape its ``cross-build/`` output into the normalized layout the dart packager + the
mobile-forge tarball consume:

    <root>/install/iOS/<target>/python-<ver>/...      # per-arch installs incl. lib-dynload
    <root>/support/<short>/iOS/Python.xcframework      # device + fat-simulator slices

where <target> is iphoneos.arm64, iphonesimulator.arm64 or iphonesimulator.x86_64.

Run from the ``darwin/`` directory:
    python build_ios.py 3.13.13
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

_LOG = None  # open file handle; set via set_log()


def set_log(path: Path) -> None:
    """Tee all subsequent run()/_emit output to a build log file."""
    global _LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOG = open(path, "w", buffering=1)
    _emit(f">>> Build log: {path}")


def _emit(line: str) -> None:
    out = line if line.endswith("\n") else line + "\n"
    sys.stdout.write(out)
    sys.stdout.flush()
    if _LOG:
        _LOG.write(out)


def run(cmd: list, cwd: Path | None = None, env: dict | None = None) -> None:
    _emit(f">>> {' '.join(str(c) for c in cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
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
        _emit(f">>> Using cached {dest.name}")
        return
    _emit(f">>> Download {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    tmp.replace(dest)


def build(version: str, short: str, minor: int, root: Path, downloads: Path,
          build_dir: Path, patches_dir: Path) -> None:
    tarball = downloads / f"Python-{version}.tgz"
    download(f"https://www.python.org/ftp/python/{version}/Python-{version}.tgz", tarball)

    src = build_dir / f"Python-{version}"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    run(["tar", "xzf", tarball, "--strip-components", "1", "-C", src])

    # < 3.14: back-port the Apple build tooling (+ runtime for 3.12) via our vendored patch.
    if minor < 14:
        patch = patches_dir / short / "Python.patch"
        if not patch.exists():
            raise SystemExit(f"No vendored iOS patch for {short} at {patch}")
        run(["patch", "-p1", "--force", "-i", patch], cwd=src)
        # `patch` doesn't preserve the executable bit, so the compiler/linker shim scripts
        # the patch adds come out non-executable. Restore +x (beeware's Makefile does the
        # same) or configure fails with "C compiler cannot create executables".
        for bindir in list(src.glob("Apple/*/Resources/bin")) + [src / "iOS" / "Resources" / "bin"]:
            for shim in bindir.glob("*") if bindir.is_dir() else []:
                shim.chmod(0o755)

    # Re-enable _multiprocessing on iOS. CPython marks it (with _posixsubprocess and
    # _posixshmem) n/a for iOS because process *spawning* is impossible in the sandbox
    # (no usable fork/exec). But _multiprocessing itself — SemLock via sem_open, and
    # socket-based Connection/Listener — builds fine on Darwin (macOS ships it); only
    # the spawning is unusable. Flipping just this one module makes
    # `import multiprocessing[.connection/.synchronize]` succeed, fixing the import-crash
    # class (e.g. scikit-learn's sklearn.callback._transport) without pretending
    # subprocess works. Hits both the vendored-patch configure (<3.14) and upstream's
    # iOS PY_STDLIB_MOD_SET_NA (3.14+); a no-op if the string isn't present.
    configure = src / "configure"
    configure.write_text(
        configure.read_text().replace(
            "py_cv_module__multiprocessing=n/a", "py_cv_module__multiprocessing=yes"
        )
    )

    # iOS's SDK declares pipe2/dup3 in headers but doesn't provide them, and on recent
    # SDKs configure mis-detects them (esp. the simulator), then the build fails with
    # "call to undeclared function 'pipe2'". The Apple tool doesn't expose configure
    # flags, but configure honors CONFIG_SITE, and the tool copies os.environ into its
    # subprocesses — so feed the same ac_cv overrides beeware passes via a site file.
    # sem_timedwait/sem_clockwait: Darwin lacks both (the SDK may still mis-declare
    # them, like pipe2); force them off so _multiprocessing/semaphore.c takes its
    # no-timeout fallback instead of referencing symbols that won't link.
    config_site = build_dir / "ios-config.site"
    config_site.write_text(
        "ac_cv_func_pipe2=no\n"
        "ac_cv_func_dup3=no\n"
        "ac_cv_func_sem_timedwait=no\n"
        "ac_cv_func_sem_clockwait=no\n"
    )
    env = {**os.environ, "CONFIG_SITE": str(config_site)}

    # The standard Apple builder: builds the build-python, every iOS slice, downloads its
    # own C deps, and emits cross-build/iOS/Python.xcframework. `build` does not run the
    # (device/simulator) testbed.
    host_python = shutil.which("python3") or sys.executable
    run([host_python, "Apple", "build", "iOS"], cwd=src, env=env)

    reshape(version, short, src, root)


def _strip(paths: list) -> None:
    """`strip -x` the given Mach-O files (removes local symbols, keeps exported ones)."""
    files = [str(p) for p in paths if p.is_file() and not p.is_symlink()]
    if files:
        run(["strip", "-x", *files])


def _embed_ios_version_in_host_triple(sysconfig_path: Path) -> None:
    """Rewrite HOST_GNU_TYPE in a _sysconfigdata file to embed IPHONEOS_DEPLOYMENT_TARGET
    (e.g. ``aarch64-apple-ios`` -> ``aarch64-apple-ios13.0``,
    ``...-ios-simulator`` -> ``...-ios13.0-simulator``)."""
    text = sysconfig_path.read_text()
    m = re.search(r"'IPHONEOS_DEPLOYMENT_TARGET':\s*'([^']+)'", text)
    if not m:
        return
    deploy = m.group(1)

    def repl(mo: "re.Match") -> str:
        triple = mo.group(1)
        if "-apple-ios" not in triple or re.search(r"-apple-ios\d", triple):
            return mo.group(0)  # not iOS, or already versioned
        return "'HOST_GNU_TYPE': '" + triple.replace("-apple-ios", f"-apple-ios{deploy}", 1) + "'"

    new = re.sub(r"'HOST_GNU_TYPE':\s*'([^']*)'", repl, text)
    if new != text:
        sysconfig_path.write_text(new)


# xcframework slice + per-arch lib dir for each of our three install targets.
XCF_SLICE_FOR_TARGET = {
    "iphoneos.arm64": ("ios-arm64", "lib-arm64"),
    "iphonesimulator.arm64": ("ios-arm64_x86_64-simulator", "lib-arm64"),
    "iphonesimulator.x86_64": ("ios-arm64_x86_64-simulator", "lib-x86_64"),
}


def reshape(version: str, short: str, src: Path, root: Path) -> None:
    """Map the Apple tool's cross-build output into our normalized layout.

    The Apple xcframework keeps the pure stdlib once (``lib/python<short>``) and the
    arch-specific bits (``lib-dynload`` + ``_sysconfigdata``) per slice in
    ``<slice>/lib-<arch>/python<short>``. The dart packager instead wants, per arch, a full
    ``install/iOS/<target>/python-<ver>/lib/python<short>`` (pure stdlib + that arch's
    lib-dynload + sysconfig), so we stitch those together here.
    """
    cross = src / "cross-build"
    pyver = f"python{short}"

    dst_xcf = root / "support" / short / "iOS" / "Python.xcframework"
    if dst_xcf.exists():
        shutil.rmtree(dst_xcf)
    dst_xcf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cross / "iOS" / "Python.xcframework", dst_xcf, symlinks=True)

    # The Apple tool configures --host=arm64-apple-ios (no version), so HOST_GNU_TYPE in
    # the _sysconfigdata lacks the iOS deployment version. mobile-forge's crossenv recovers
    # the iOS release by splitting HOST_GNU_TYPE (when _PYTHON_HOST_PLATFORM isn't set), so a
    # version-less triple yields an empty release and pip's iOS-tag code crashes. Embed the
    # version (e.g. aarch64-apple-ios -> aarch64-apple-ios13.0), matching beeware. Fixing the
    # xcframework copies here means the dart-install + platform-config copies inherit it.
    for sc in dst_xcf.glob(f"*/lib-*/{pyver}/_sysconfigdata__ios_*.py"):
        _embed_ios_version_in_host_triple(sc)

    shared_stdlib = dst_xcf / "lib" / pyver
    if not (shared_stdlib / "os.py").exists():
        raise SystemExit(f"Expected shared stdlib at {shared_stdlib}")

    for target, (slice_name, arch_lib) in XCF_SLICE_FOR_TARGET.items():
        arch_dir = dst_xcf / slice_name / arch_lib / pyver
        if not (arch_dir / "lib-dynload").is_dir():
            raise SystemExit(f"Expected per-arch lib-dynload at {arch_dir}")
        dst = root / "install" / "iOS" / target / f"python-{version}" / "lib" / pyver
        if dst.parent.parent.exists():
            shutil.rmtree(dst.parent.parent)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Pure stdlib (its lib-dynload is empty), then overlay this arch's real lib-dynload
        # + _sysconfigdata.
        shutil.copytree(shared_stdlib, dst, symlinks=True)
        if (dst / "lib-dynload").exists():
            shutil.rmtree(dst / "lib-dynload")
        shutil.copytree(arch_dir / "lib-dynload", dst / "lib-dynload", symlinks=True)
        for sysconfig in arch_dir.glob("_sysconfigdata*.py"):
            shutil.copyfile(sysconfig, dst / sysconfig.name)
        # The Apple tool leaves the iOS binaries unstripped; strip local symbols (keeps the
        # exported PyInit_/C-API symbols needed for dynamic linking). These .so become the
        # per-module python-xcframeworks; Xcode re-signs them when embedding into the app.
        _strip([*(dst / "lib-dynload").glob("*.so")])

    # Strip the per-slice framework binary too (the libpython that ships in the xcframework).
    _strip([dst_xcf / sl / "Python.framework" / "Python"
            for sl in {s for s, _ in XCF_SLICE_FOR_TARGET.values()}])

    deps = reshape_mobile_forge(version, short, cross, dst_xcf, root)

    support = root / "support" / short / "iOS"
    support.mkdir(parents=True, exist_ok=True)
    lines = [f"Python version: {version}", "Build: custom", "Min iOS version: 13.0"]
    if deps:
        lines.append("---------------------")
        lines += [f"{name}: {ver}" for name, ver in sorted(deps.items())]
    (support / "VERSIONS").write_text("\n".join(lines) + "\n")


# Map cpython-apple-source-deps lib name -> the VERSIONS label mobile-forge greps for
# (case-insensitive, so exact casing is cosmetic; match beeware's labels).
DEP_LABELS = {
    "openssl": "OpenSSL", "libffi": "libFFI", "xz": "XZ",
    "bzip2": "BZip2", "mpdecimal": "mpdecimal", "zstd": "zstd",
}


def reshape_mobile_forge(version: str, short: str, cross: Path, dst_xcf: Path,
                         root: Path) -> dict:
    """Add what flet-dev/mobile-forge needs (beyond the dart inputs):

    * per-arch C-dependency dirs ``install/iOS/<arch>/<dep>-<ver>/`` (re-extracted from the
      tarballs the Apple tool already downloaded), so mobile-forge can build dep wheels;
    * in each xcframework slice, a stub ``bin/python<short>`` and the ``_sysconfigdata`` at
      ``platform-config/<arch>-<sdk>/`` — the spots mobile-forge's crossenv setup looks for
      (the Apple tool only leaves them under ``lib-<arch>/``).

    Returns {dep-label: version-build} for the VERSIONS file.
    """
    pyver = f"python{short}"
    targets = ("iphoneos.arm64", "iphonesimulator.arm64", "iphonesimulator.x86_64")

    # 1. Per-arch dependency dirs from the downloaded tarballs.
    deps: dict[str, str] = {}
    downloads = cross / "downloads"
    pattern = re.compile(r"^(?P<dep>[a-z0-9]+)-(?P<ver>\d[^-]*-\d+)-(?P<target>.+)\.tar\.gz$")
    for tarball in sorted(downloads.glob("*.tar.gz")) if downloads.is_dir() else []:
        m = pattern.match(tarball.name)
        if not m or m["target"] not in targets:
            continue
        dep, ver, target = m["dep"], m["ver"], m["target"]
        dest = root / "install" / "iOS" / target / f"{dep}-{ver}"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        run(["tar", "xzf", tarball, "-C", dest])
        deps[DEP_LABELS.get(dep, dep)] = ver

    # 2. Per-slice stub python + platform-config sysconfigdata.
    for slice_dir in {sl for sl, _ in XCF_SLICE_FOR_TARGET.values()}:
        slice_path = dst_xcf / slice_dir
        stub = slice_path / "bin" / f"python{short}"
        stub.parent.mkdir(parents=True, exist_ok=True)
        if not stub.exists():
            stub.write_text(f"#!/bin/bash\necho \"Can't run {slice_dir} binary\"\nexit 1\n")
            stub.chmod(0o755)
        # Copy each arch's _sysconfigdata into platform-config/<arch>-<sdk>/ where
        # CrossVEnv.create() looks for it.
        for sysconfig in slice_path.glob(f"lib-*/{pyver}/_sysconfigdata__ios_*.py"):
            m = re.match(r"_sysconfigdata__ios_(?P<arch>.+)-(?P<sdk>iphoneos|iphonesimulator)\.py",
                         sysconfig.name)
            if not m:
                continue
            pc = slice_path / "platform-config" / f"{m['arch']}-{m['sdk']}"
            pc.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sysconfig, pc / sysconfig.name)

    return deps


def main() -> None:
    if platform.system() != "Darwin":
        raise SystemExit("build_ios.py must run on macOS.")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="full CPython version, e.g. 3.13.13")
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
    build_dir = root / "build" / "iOS"
    build_dir.mkdir(parents=True, exist_ok=True)
    set_log(build_dir / f"build-ios-{version}.log")

    if minor >= 14:
        _emit(f">>> iOS {version}: native CPython Apple tooling")
    else:
        _emit(f">>> iOS {version}: Apple tooling back-ported via vendored patch")

    build(version, short, minor, root, downloads, build_dir, script_dir / "ios_patches")

    _emit(f"\n>>> iOS build complete for {version}")
    _emit(f"    xcframework: {root / 'support' / short / 'iOS' / 'Python.xcframework'}")
    _emit(f"    installs:    {root / 'install' / 'iOS'}")


if __name__ == "__main__":
    main()
