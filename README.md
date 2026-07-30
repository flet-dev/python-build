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
64-bit (`arm64-v8a`, `x86_64`) plus 32-bit `armeabi-v7a`. [PEP 738](https://peps.python.org/pep-0738/)
makes 64-bit Android the *tested* Tier-3 configuration, but CPython's official
`Android/android.py` still builds 32-bit ARM (it's in its supported `HOSTS`), so
all three minors carry `armeabi-v7a`. Drop an ABI from a minor's list to stop
building it.

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

### Apple XCFramework signing

Every `.xcframework` in the Darwin tarballs is provider-signed with the Flet
publishing team's Apple Distribution identity and a secure timestamp — **both the
inner `.framework` bundles in each slice and the outer xcframework**, in that
order.

Signing only the outer bundle is not enough: an IPA built against such an
artifact reports `signed = true` but `isSecureTimestamp = false`. Every slice of
an XCFramework Apple's App Store scan demonstrably accepts
([krzyzanowskim/OpenSSL](https://github.com/krzyzanowskim/OpenSSL)) carries its
own signature, with the outer bundle signed last. The order matters — the outer
seal hashes the bundle contents, so signing an inner framework afterwards
invalidates it.

This matters because Xcode records the state of each `.xcframework` an app links
against **as its publisher shipped it**, and writes the result into the IPA as
`Signatures/<name>.xcframework-ios.signature`. Unsigned artifacts make those
receipts read `signed = false` / `isSecureTimestamp = false`, which Apple's App
Store scan reports as `ITMS-91065: Missing signature`. The app's own signature is
a separate thing and does not fill it in.

Signing runs in `sign-darwin-artifacts`, a release-only job gated on a
`workflow_dispatch` with a `release_date` **from `main`**, using the protected
`release-signing` environment. It downloads the finished Darwin archives, signs
every xcframework inside them, re-packs, and re-verifies after extraction — so the
certificate is never present in the build matrix that runs on pushes and PRs,
while the "sign only after every mutation" ordering is still guaranteed.

The unsigned build artifacts are named `darwin-unsigned-*` rather than
`python-darwin-*` specifically so that no `python-*` download pattern in the
publish job can reach them. `publish-release` downloads Android/Linux/Windows
artifacts by explicit pattern plus the `python-darwin-signed` bundle, and fails if
the expected Darwin tarballs are absent.

Framework bundle identifiers are assigned here, before signing, and are
publisher-owned and stable: `dev.flet.python.runtime` for the runtime and
`dev.flet.python.<module>` for each stdlib extension. They must not depend on the
consuming app — see `darwin/xcframework_identifiers.sh`, which validates that they
are unique, syntactically valid, and consistent across device and simulator slices.

Local builds without signing credentials still work and produce unsigned artifacts;
`REQUIRE_XCFRAMEWORK_SIGNATURE=1` (set on the release path) turns every missing
credential, missing timestamp, wrong team, or empty archive into a hard failure.

## Consumers

- **serious_python** pins a release date, fetches that release's `manifest.json`,
  and generates committed per-platform version tables from it.
- **flet** fetches the manifest by date for commands that run without serious_python
  (e.g. `flet publish`).

See platform-specific notes under [`android/`](android/README.md),
[`darwin/`](darwin/README.rst), `linux/`, and `windows/`.
