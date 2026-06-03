Darwin (iOS / macOS) Python builds
==================================

Builds embeddable iOS and macOS Python runtimes for Flet, packaged for the Dart/Flutter
side and for `mobile-forge <https://github.com/flet-dev/mobile-forge>`__.

Both ``build_ios.py`` and ``build_macos.py`` emit the same normalized layout, which the
``package-*-for-dart.sh`` scripts (and the mobile-forge tarball) consume::

    install/iOS/<target>/python-<ver>/...        # per-arch installs incl. lib-dynload
    install/macOS/macosx/python-<ver>/Python.framework
    support/<short>/iOS/Python.xcframework        # device + fat-simulator slices
    support/<short>/macOS/Python.xcframework      # universal2 (macos-arm64_x86_64)

where ``<target>`` is ``iphoneos.arm64``, ``iphonesimulator.arm64`` or
``iphonesimulator.x86_64``.

iOS — ``build_ios.py <full-version>``
-------------------------------------

Every version is built with the **same** mechanism — CPython's in-tree ``Apple`` build
tool (``python Apple build iOS``) — then ``cross-build/`` is reshaped into the layout above:

* **3.14+** — the ``Apple/`` tooling is native; build straight from the source tarball.
* **3.13 / 3.12** — the tooling (and, for 3.12, the PEP 730 iOS runtime) isn't upstream
  yet, so a vendored back-port patch (``ios_patches/<short>/Python.patch``, see that
  directory's README) is applied first to add it. No dependency on beeware's
  Python-Apple-support repo.

The Apple tool downloads its own pre-compiled C deps (from
`cpython-apple-source-deps <https://github.com/beeware/cpython-apple-source-deps>`__ — the
URL CPython itself hard-codes, for every version), builds all slices, and creates the
xcframework. Output is tee'd to ``build/iOS/build-ios-<ver>.log``.

The reshape also rebuilds the ``install``/``support`` tree that
`mobile-forge <https://github.com/flet-dev/mobile-forge>`__ consumes (the
``python-ios-mobile-forge-<short>.tar.gz`` artifact): per-arch ``install/iOS/<arch>/<dep>-<ver>/``
dirs (re-extracted from the deps the Apple tool downloaded) + a ``support/<short>/iOS/VERSIONS``
listing those versions, and in each xcframework slice a stub ``bin/python<short>`` plus the
``_sysconfigdata`` under ``platform-config/<arch>-<sdk>/`` — the spots mobile-forge's
``crossenv`` setup looks for. (The dart packager excludes all of these from its bundle.)

macOS — ``build_macos.py <full-version>``
-----------------------------------------

Builds a universal2 ``Python.framework`` **from source** for all versions, then makes it
relocatable (``@rpath``), codesigns it, and wraps it in an xcframework. Building from
source (rather than re-bundling python.org's official ``.pkg``) means we get the exact
micro version even when no macOS installer was published for it.

The macOS SDK supplies headers for bz2/sqlite/zlib/libffi and CPython bundles libmpdec,
but it ships **no OpenSSL** and **no lzma.h** (only ``liblzma.dylib``), so **OpenSSL** and
**xz/liblzma** are built universal2 from source here (``--openssl-version`` / ``--xz-version``).
To match the official/beeware layout, OpenSSL is built **shared** and bundled into the
framework (``Versions/<short>/lib/lib{ssl,crypto}.3.dylib``) with ``@rpath`` install names,
which ``_ssl``/``_hashlib`` reference (the embedding host — e.g. serious_python — provides
the rpath, exactly as the released artifact does); xz is linked **statically** into
``_lzma``. On 3.14+, libzstd is also built from source (``--zstd-version``) and linked
statically into ``_zstd`` (new in 3.14). All binaries are stripped (``strip -x``) before
codesigning. Pass
``--app-store-compliance`` to apply ``macos_support/app-store-compliance.patch`` (off by
default; only for Mac App Store). Full build output is tee'd to
``build/macOS/build-macos-<ver>.log``.

Packaging
---------

``package-ios-for-dart.sh . <short>`` and ``package-macos-for-dart.sh . <short>`` turn the
normalized layout into the Dart-consumable ``dist/python-{ios,macos}-dart-<short>.tar.gz``
archives, reusing ``xcframework_utils.sh``, ``Modules/`` and the ``*.exclude`` lists.

This whole flow is driven in CI by ``.github/workflows/build-python-version.yml``.
