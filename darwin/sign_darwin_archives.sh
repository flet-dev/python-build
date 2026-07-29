#!/usr/bin/env bash
set -euo pipefail
#
# sign_darwin_archives.sh <out-dir> <archive.tar.gz>...
#
# Provider-sign every outer XCFramework inside already-built Darwin archives and
# emit re-packed, verified copies into <out-dir>.
#
# WHY OPERATE ON THE ARCHIVES AND NOT INSIDE THE BUILD
#   Signing must be the LAST thing that happens to a bundle, and it needs a
#   release certificate. Doing it here means the certificate never has to be
#   present in the general build matrix (which runs on every push and PR), while
#   still guaranteeing the ordering: by the time an archive exists, every
#   mutation — install names, Info.plists, privacy manifests, header overlays,
#   pruning, stripping, bytecode compilation — is finished.
#
# WHAT IT DOES per archive
#   1. extract into a scratch directory
#   2. validate bundle identifiers (valid, unique, per-slice consistent, and
#      provider-owned — never derived from a consuming app)
#   3. sign every outer *.xcframework, verifying each immediately
#   4. re-pack, preserving the original member-path shape
#   5. extract the re-packed archive into a FRESH directory and verify again
#
# Step 5 is the one that matters: consumers get the tarball, not the directory
# we signed, so an archiving step that mangles symlinks or permissions must fail
# here rather than in someone's app.
#
# Environment: see xcframework_signing.sh. Release runs set
# REQUIRE_XCFRAMEWORK_SIGNATURE=1, which turns every missing credential, missing
# timestamp, wrong team, or empty archive into a hard failure.

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=darwin/xcframework_signing.sh
. "$script_dir/xcframework_signing.sh"
# shellcheck source=darwin/xcframework_identifiers.sh
. "$script_dir/xcframework_identifiers.sh"

out_dir=${1:?usage: sign_darwin_archives.sh <out-dir> <archive.tar.gz>...}
shift
[ "$#" -gt 0 ] || { echo "sign_darwin_archives.sh: no archives given" >&2; exit 1; }

mkdir -p "$out_dir"
out_dir=$(cd "$out_dir" && pwd -P)

preflight_rc=0
xcf_signing_preflight || preflight_rc=$?
[ "$preflight_rc" -le 1 ] || exit 1

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

for archive in "$@"; do
    [ -f "$archive" ] || { echo "sign_darwin_archives.sh: not a file: $archive" >&2; exit 1; }
    base=$(basename "$archive")
    echo "=== $base ==="

    src="$work/src"
    rm -rf "$src"; mkdir -p "$src"
    tar -xzf "$archive" -C "$src"

    # Re-packing must reproduce the original member paths. `tar -czf ... -C dir .`
    # writes "./install/..." while `tar -czf ... install support` writes
    # "install/...", and consumers that extract with --wildcards patterns (e.g.
    # dart-bridge's CI pulling "install/android/...") match one shape and not the
    # other. Read which shape this archive uses instead of guessing.
    listing="$work/listing.txt"
    tar -tzf "$archive" > "$listing"
    if head -1 "$listing" | grep -q '^\./'; then
        dot_prefixed=1
    else
        dot_prefixed=0
    fi

    xcf_validate_identifiers "$src"
    xcf_sign_tree "$src"

    if [ "$dot_prefixed" -eq 1 ]; then
        ( cd "$src" && COPYFILE_DISABLE=1 tar -czf "$out_dir/$base" . )
    else
        # Explicit top-level members, so the archive keeps its original entry
        # names. Dotfiles at the root would be missed by a bare `*`, so enumerate.
        tops=()
        while IFS= read -r top; do
            [ -n "$top" ] && tops+=("$top")
        done < <(cd "$src" && ls -A)
        [ "${#tops[@]}" -gt 0 ] || { echo "sign_darwin_archives.sh: $base extracted to nothing" >&2; exit 1; }
        ( cd "$src" && COPYFILE_DISABLE=1 tar -czf "$out_dir/$base" -- "${tops[@]}" )
    fi

    rt="$work/roundtrip"
    rm -rf "$rt"; mkdir -p "$rt"
    tar -xzf "$out_dir/$base" -C "$rt"
    xcf_verify_tree "$rt"

    echo "=== $base: done -> $out_dir/$base ==="
done

echo "Signed archives in $out_dir:"
ls -lh "$out_dir"
