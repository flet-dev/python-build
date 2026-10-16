# iOS source patches

Vendored patches that back-port CPython's in-tree Apple build tooling (the `Apple/`
package — `python Apple build iOS`) onto older CPython versions, so `build_ios.py` can use
the **same** standard mechanism for every version instead of bespoke build code.

| Patch | Back-ports | Notes |
|---|---|---|
| `3.13/Python.patch` | `Apple/` build tooling + `iOS/Resources/bin` shims | iOS *runtime* (PEP 730) is already upstream in 3.13 |
| `3.12/Python.patch` | `Apple/` build tooling **+ the PEP 730 runtime** (`_ios_support.py`, `getpath.c`, `pylifecycle.c`, …) | 3.12 has no iOS support at all upstream |

3.14+ needs **no** patch — the `Apple/` tooling is native there.

## Provenance

Originally derived from [beeware/Python-Apple-support](https://github.com/beeware/Python-Apple-support)
`patch/Python/Python.patch` on the matching `3.X` branch. We vendor them here so the build
has **no runtime dependency on the Python-Apple-support repo** and so we can adjust them for
micro releases ourselves.

(Note: the build still downloads pre-compiled C dependencies — OpenSSL/libffi/xz/… — from
`beeware/cpython-apple-source-deps`, because CPython's own `Apple/__main__.py` hard-codes
that URL for *all* versions, including native 3.14. That is the upstream-standard binary-dep
source and is out of scope here.)

## Maintaining across releases

The patches are applied with `patch -p1`. They generally apply across micro releases
unchanged, but a micro bump can drift a hunk (usually in `configure`, or a `Lib/test/` file).
When that happens:

1. Extract the target source: `tar xzf Python-<ver>.tgz`.
2. `cd` in and `patch -p1 --force < <this>/Python.patch`; inspect any `*.rej`.
3. Fix the hunk in the patch (or drop it if it only touches `Lib/test/`, which is neither
   built nor shipped — that is exactly why the 3.12 patch here has had its
   `Lib/test/test_genericpath.py` hunk removed for 3.12.13).

The 3.12 patch also carries one fix that isn't about iOS: `Modules/_ssl.c` reads
`ASN1_STRING` fields through `ASN1_STRING_length()` / `ASN1_STRING_get0_data()` instead of
the struct members, as 3.13+ does upstream. OpenSSL 4 made `ASN1_STRING` opaque, and the
native build-python that the Apple tool compiles first picks up whatever OpenSSL Homebrew
links (`openssl@4` on current GitHub macOS runners), so without it the 3.12 build fails in
`_ssl.c`. The accessors exist since OpenSSL 1.1.0, so the iOS slices (OpenSSL 3.x) are
unaffected.

A minor-version bump (e.g. adding 3.15) means refreshing from the corresponding upstream
back-port branch.
