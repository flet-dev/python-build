# python-build

Builds the embedded CPython runtimes (Android, iOS, macOS, Linux, Windows) that
[serious_python](https://github.com/flet-dev/serious-python) and
[flet](https://github.com/flet-dev/flet) bundle into apps.

## `manifest.json` — the runtime source of truth

[`manifest.json`](manifest.json) is the single source of truth for a runtime
release. It is used **both** ways:

- **Drives the build.** CI reads it to decide which CPython versions to build
  (the build matrix), so what gets built is exactly what the manifest lists.
- **Is published.** Each release uploads the same `manifest.json` as a release
  asset (with the `release` date injected), so downstream tools fetch a single,
  consistent version set by date instead of hand-mirroring versions.

Schema:

```json
{
  "release": "20260611",                  // injected at publish (= release tag)
  "default_python_version": "3.14",
  "dart_bridge_version": "1.2.3",
  "pythons": {
    "3.14": {
      "full_version": "3.14.6",
      "standalone_release_date": "20260610",
      "pyodide_version": "314.0.0",
      "pyodide_platform_tag": "pyemscripten-2026.0-wasm32",
      "android_abis": ["arm64-v8a", "x86_64"],
      "prerelease": false
    }
  }
}
```

`android_abis` lists the ABIs `package-for-dart.sh` builds for this minor —
64-bit first, then `armeabi-v7a` if the minor still supports 32-bit Android.
3.12 carries all three; 3.13+ are 64-bit-only ([PEP 738](https://peps.python.org/pep-0738/)).

The committed file omits `release`; the publish step injects it.

### Adding or bumping a Python / Pyodide / dart_bridge version

Edit [`manifest.json`](manifest.json) and open a PR. The CI matrix updates
automatically from it.

## Releases

Releases are date-keyed (`YYYYMMDD`) and cut manually:

1. Run the **Build Python Packages** workflow via `workflow_dispatch` with a
   `release_date` of `YYYYMMDD`.
2. CI builds every Python in the manifest matrix, then publishes all per-platform
   tarballs **and** `manifest.json` (with `release` set) to a GitHub release tagged
   with that date.

A push without a `release_date` still exercises the full matrix but publishes no
release (per-job artifacts only).

## Consumers

- **serious_python** pins a release date, fetches that release's `manifest.json`,
  and generates committed per-platform version tables from it.
- **flet** fetches the manifest by date for commands that run without serious_python
  (e.g. `flet publish`).

See platform-specific notes under [`android/`](android/README.md),
[`darwin/`](darwin/README.rst), `linux/`, and `windows/`.
