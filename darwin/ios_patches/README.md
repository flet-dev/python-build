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

A minor-version bump (e.g. adding 3.15) means refreshing from the corresponding upstream
back-port branch.
