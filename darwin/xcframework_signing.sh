#!/usr/bin/env bash
#
# Provider-signing helpers for Apple XCFrameworks. Source this file; it defines
# functions only and never signs anything on its own.
#
# WHY THIS EXISTS
#   Xcode records the state of every XCFramework an app links against at the
#   moment it is consumed, and writes the result into the IPA's top-level
#   `Signatures/<name>.xcframework-<platform>.signature` receipts. If the
#   XCFramework we publish is unsigned, that receipt says `signed = false` /
#   `isSecureTimestamp = false` no matter how the app itself is signed —
#   app-signing the embedded inner framework does NOT retroactively supply an
#   SDK-origin signature. Apple's App Store scan reports the gap as
#   `ITMS-91065: Missing signature`.
#   See https://developer.apple.com/documentation/Xcode/verifying-the-origin-of-your-xcframeworks
#
# ORDER MATTERS
#   Signing seals the whole bundle by content hash. Every mutation — install
#   names, Info.plists, privacy manifests, headers, pruning, stripping — must be
#   finished BEFORE the outer `.xcframework` is signed, and nothing inside it may
#   change afterwards. Sign last, verify, archive, then verify again after an
#   archive round trip.
#
# INPUTS (environment)
#   XCFRAMEWORK_CODESIGN_IDENTITY   Certificate SHA-1 fingerprint of the Apple
#                                   Distribution identity to sign with. A
#                                   fingerprint, not a display name: display
#                                   names are ambiguous when a keychain holds
#                                   more than one matching certificate, and
#                                   codesign then picks arbitrarily.
#   XCFRAMEWORK_SIGNING_KEYCHAIN    Path to the keychain holding that identity.
#                                   Optional; the default search list is used
#                                   when unset.
#   XCFRAMEWORK_EXPECTED_TEAM_ID    10-character Team ID asserted on the
#                                   resulting signature. Required when signing.
#   XCFRAMEWORK_EXPECTED_AUTHORITY  Substring every signature's authority chain
#                                   must contain. Default "Apple Distribution".
#   REQUIRE_XCFRAMEWORK_SIGNATURE   Set to 1 for release builds: missing
#                                   credentials, a missing timestamp, a wrong
#                                   team, or an empty set of XCFrameworks all
#                                   become hard failures instead of a skip.
#
# Local and PR builds supply none of these and produce unsigned artifacts, which
# is fine for testing and never publishable.

xcf_log()  { echo "xcframework-signing: $*"; }
xcf_warn() { echo "xcframework-signing: $*" >&2; }
xcf_err()  { echo "xcframework-signing: ERROR: $*" >&2; }

# True when a signing identity has been supplied.
xcf_signing_configured() { [ -n "${XCFRAMEWORK_CODESIGN_IDENTITY:-}" ]; }

# True when this build must not publish an unsigned artifact.
xcf_signing_required() { [ "${REQUIRE_XCFRAMEWORK_SIGNATURE:-0}" = "1" ]; }

# Validate the signing configuration once, up front, so a release build fails
# before spending minutes compiling rather than at the very last step.
#
# Returns 0 when signing is configured, 1 when it is not. Callers that must not
# proceed unsigned check xcf_signing_required themselves (or call
# xcf_sign_tree, which enforces it).
xcf_signing_preflight() {
    if ! xcf_signing_configured; then
        if xcf_signing_required; then
            xcf_err "REQUIRE_XCFRAMEWORK_SIGNATURE=1 but XCFRAMEWORK_CODESIGN_IDENTITY is empty"
            return 2
        fi
        xcf_warn "no XCFRAMEWORK_CODESIGN_IDENTITY; artifacts will be UNSIGNED (not publishable)"
        return 1
    fi

    if [ -z "${XCFRAMEWORK_EXPECTED_TEAM_ID:-}" ]; then
        xcf_err "XCFRAMEWORK_CODESIGN_IDENTITY is set but XCFRAMEWORK_EXPECTED_TEAM_ID is empty;" \
                "refusing to sign without a team to verify against"
        return 2
    fi

    if [ -n "${XCFRAMEWORK_SIGNING_KEYCHAIN:-}" ] && [ ! -f "$XCFRAMEWORK_SIGNING_KEYCHAIN" ]; then
        xcf_err "keychain not found: $XCFRAMEWORK_SIGNING_KEYCHAIN"
        return 2
    fi

    # Confirm the fingerprint actually resolves to a codesigning identity before
    # the first `codesign` call, so a mis-imported certificate is reported as
    # such instead of as "no identity found" from deep inside a build.
    local identities
    identities=$(security find-identity -v -p codesigning ${XCFRAMEWORK_SIGNING_KEYCHAIN:+"$XCFRAMEWORK_SIGNING_KEYCHAIN"} 2>&1) || {
        xcf_err "security find-identity failed: $identities"
        return 2
    }
    if ! printf '%s\n' "$identities" | grep -qF "$XCFRAMEWORK_CODESIGN_IDENTITY"; then
        xcf_err "identity $XCFRAMEWORK_CODESIGN_IDENTITY not present in" \
                "${XCFRAMEWORK_SIGNING_KEYCHAIN:-the default keychain search list}"
        printf '%s\n' "$identities" >&2
        return 2
    fi

    xcf_log "signing with $XCFRAMEWORK_CODESIGN_IDENTITY (team ${XCFRAMEWORK_EXPECTED_TEAM_ID})"
    return 0
}

# Enumerate every *.xcframework at or below the given roots, NUL-separated.
#
# -prune stops the walk at each match: only the OUTER bundle is a signing
# target, and re-signing something nested inside one would break the outer
# seal. NUL separation because module names come from arbitrary wheels.
xcf_find() {
    local root
    for root in "$@"; do
        [ -e "$root" ] || continue
        find "$root" -name '*.xcframework' -prune -print0
    done
}

# The identifier to seal the OUTER bundle under.
#
# An .xcframework's root Info.plist is an XFWK manifest: it carries
# AvailableLibraries, CFBundlePackageType and XCFrameworkFormatVersion, and no
# CFBundleIdentifier at all. Left alone, codesign falls back to the bundle's file
# name, so the seal reports a bare `Identifier=dart_bridge` instead of a
# reverse-DNS one. Read the identifier off the xcframework's own inner framework
# instead: it is already stable and provider-owned in every artifact we publish,
# so the outer seal and the framework it wraps agree by construction and neither
# depends on the consuming application.
xcf_signing_identifier() {
    local xcf=$1
    local name plist ident
    name=$(basename "$xcf" .xcframework)

    local slice
    for slice in "$xcf"/*/; do
        [ -d "$slice$name.framework" ] || continue
        # Flat (iOS) layout, then versioned (macOS). Versions/Current is a
        # symlink to the real version directory, so skip it.
        for plist in "$slice$name.framework/Info.plist" \
                     "$slice$name.framework"/Versions/*/Resources/Info.plist; do
            [ -f "$plist" ] || continue
            case "$plist" in */Versions/Current/*) continue ;; esac
            ident=$(plutil -extract CFBundleIdentifier raw -o - "$plist" 2>/dev/null) || continue
            if [ -n "$ident" ]; then
                printf '%s' "$ident"
                return 0
            fi
        done
    done
    return 1
}

# Emit the per-slice <Name>.framework bundles inside an xcframework.
xcf_slice_frameworks() {
    local xcf=$1
    local name
    name=$(basename "$xcf" .xcframework)

    local slice
    for slice in "$xcf"/*/; do
        [ -d "$slice$name.framework" ] && printf '%s\n' "$slice$name.framework"
    done
    return 0
}

# Sign one completed outer XCFramework — inner frameworks first, outer last.
#
# WHY BOTH LAYERS
#   Signing only the outer bundle is not enough. Compare against a third-party
#   XCFramework Apple's App Store scan demonstrably accepts
#   (github.com/krzyzanowskim/OpenSSL): every one of its ten slices carries its
#   own Apple Distribution signature with a secure timestamp, and the outer
#   bundle is signed afterwards. Our artifacts previously had unsigned inner
#   frameworks, and their IPA receipts came back `signed = true` but
#   `isSecureTimestamp = false` — the one structural difference between the two.
#
#   Order matters and is not optional: the outer seal hashes the bundle contents,
#   so the inner signatures have to exist before it is computed. Signing an inner
#   framework afterwards would invalidate the outer seal, which is the same
#   mistake this whole effort exists to stop making.
#
#   `xcodebuild -create-xcframework` preserves inner `_CodeSignature`
#   directories, so a producer may equally sign the .framework bundles before
#   assembling the xcframework; doing it here keeps one code path for both the
#   build-time and the sign-the-finished-archive callers.
xcf_sign_one() {
    local xcf=$1
    [ -d "$xcf" ] || { xcf_err "not a directory: $xcf"; return 1; }

    local ident
    if ! ident=$(xcf_signing_identifier "$xcf"); then
        xcf_err "$xcf: no inner framework Info.plist with a CFBundleIdentifier;" \
                "cannot derive a signing identifier"
        return 1
    fi

    local args=(--force --timestamp --sign "$XCFRAMEWORK_CODESIGN_IDENTITY")
    [ -n "${XCFRAMEWORK_SIGNING_KEYCHAIN:-}" ] && args+=(--keychain "$XCFRAMEWORK_SIGNING_KEYCHAIN")

    # Deliberately no --deep anywhere below: it re-signs nested code with the
    # outer bundle's options and is documented by Apple as inappropriate for
    # producing a distributable signature. Signing each slice explicitly is the
    # supported way to get the same coverage. Deliberately no --timestamp=none:
    # the receipt's isSecureTimestamp is exactly what we are here to make true.
    local fw count=0
    while IFS= read -r fw; do
        [ -n "$fw" ] || continue
        # No -i for the inner bundles: a .framework has a real CFBundleIdentifier
        # in its own Info.plist, which codesign picks up.
        codesign "${args[@]}" "$fw" || { xcf_err "codesign failed for $fw"; return 1; }
        count=$((count + 1))
    done <<EOF
$(xcf_slice_frameworks "$xcf")
EOF

    if [ "$count" -eq 0 ]; then
        xcf_err "$xcf: no slice frameworks found to sign"
        return 1
    fi

    xcf_log "signing $xcf as $ident ($count slice framework(s) + outer)"
    codesign "${args[@]}" -i "$ident" "$xcf" || { xcf_err "codesign failed for $xcf"; return 1; }
}

# Assert one signed bundle (inner framework or outer xcframework) carries a real
# provider signature: verifiable, not ad-hoc, securely timestamped, right
# authority, right team.
xcf_assert_signature() {
    local target=$1
    local expect_team=${XCFRAMEWORK_EXPECTED_TEAM_ID:-}
    local expect_authority=${XCFRAMEWORK_EXPECTED_AUTHORITY:-Apple Distribution}

    if ! codesign --verify --strict --verbose=4 "$target" 2>&1; then
        xcf_err "$target: codesign --verify --strict failed"
        return 1
    fi

    local info
    if ! info=$(codesign -dvvv "$target" 2>&1); then
        xcf_err "$target: codesign -dvvv failed: $info"
        return 1
    fi

    if printf '%s\n' "$info" | grep -q '^Signature=adhoc'; then
        xcf_err "$target: ad-hoc signature; a release artifact needs a real identity"
        return 1
    fi

    # `Timestamp=` is the secure (Apple TSA) timestamp. A signature made without
    # --timestamp reports `Signed Time=` instead, which is self-asserted and is
    # what makes an IPA receipt report isSecureTimestamp = false.
    if ! printf '%s\n' "$info" | grep -q '^Timestamp='; then
        xcf_err "$target: no secure timestamp (signed without --timestamp?)"
        return 1
    fi

    if ! printf '%s\n' "$info" | grep '^Authority=' | grep -qF "$expect_authority"; then
        xcf_err "$target: no '$expect_authority' authority in the signature chain"
        return 1
    fi

    if [ -n "$expect_team" ]; then
        local actual_team
        actual_team=$(printf '%s\n' "$info" | sed -n 's/^TeamIdentifier=//p' | head -1)
        if [ "$actual_team" != "$expect_team" ]; then
            xcf_err "$target: TeamIdentifier '$actual_team' != expected '$expect_team'"
            return 1
        fi
    fi
}

# Verify one XCFramework carries real provider signatures on BOTH layers.
xcf_verify_one() {
    local xcf=$1

    if [ ! -f "$xcf/_CodeSignature/CodeResources" ]; then
        xcf_err "$xcf: no outer _CodeSignature/CodeResources — the XCFramework is unsigned"
        return 1
    fi

    # Every slice's framework must be signed in its own right. An unsigned inner
    # bundle is what produced `isSecureTimestamp = false` receipts even though the
    # outer seal was valid — see xcf_sign_one.
    local fw count=0
    while IFS= read -r fw; do
        [ -n "$fw" ] || continue
        if [ ! -d "$fw/_CodeSignature" ] && [ ! -d "$fw/Versions/A/_CodeSignature" ]; then
            xcf_err "$fw: inner framework is unsigned"
            return 1
        fi
        xcf_assert_signature "$fw" >/dev/null || return 1
        count=$((count + 1))
    done <<EOF
$(xcf_slice_frameworks "$xcf")
EOF

    if [ "$count" -eq 0 ]; then
        xcf_err "$xcf: no slice frameworks found to verify"
        return 1
    fi

    xcf_assert_signature "$xcf" || return 1

    local info
    info=$(codesign -dvvv "$xcf" 2>&1) || info=""
    printf '%s\n' "$info"

    # The outer seal must name the same provider-owned identifier as the inner
    # framework. A mismatch means the bundle was re-signed by something that did
    # not pass -i, and fell back to the file name.
    local expect_ident actual_ident
    if expect_ident=$(xcf_signing_identifier "$xcf"); then
        actual_ident=$(printf '%s\n' "$info" | sed -n 's/^Identifier=//p' | head -1)
        if [ "$actual_ident" != "$expect_ident" ]; then
            xcf_err "$xcf: signing identifier '$actual_ident' != inner framework's '$expect_ident'"
            return 1
        fi
    fi

    xcf_log "verified $xcf"
}

# Sign every XCFramework below the given roots.
#
# Skips (with a warning) when no identity is configured, unless
# REQUIRE_XCFRAMEWORK_SIGNATURE=1 — which is what keeps local and PR builds
# working while making a release that lost its credentials fail loudly.
xcf_sign_tree() {
    if ! xcf_signing_configured; then
        if xcf_signing_required; then
            xcf_err "REQUIRE_XCFRAMEWORK_SIGNATURE=1 but no signing identity configured"
            return 1
        fi
        xcf_warn "skipping signing of: $* (no identity configured)"
        return 0
    fi

    local list status=0 count=0 xcf
    list=$(mktemp)
    xcf_find "$@" > "$list"
    while IFS= read -r -d '' xcf; do
        if ! xcf_sign_one "$xcf"; then status=1; break; fi
        if ! xcf_verify_one "$xcf"; then status=1; break; fi
        count=$((count + 1))
    done < "$list"
    rm -f "$list"

    [ "$status" -eq 0 ] || return 1
    if [ "$count" -eq 0 ]; then
        xcf_err "no *.xcframework found under: $*"
        return 1
    fi
    xcf_log "signed and verified $count xcframework(s) under: $*"
}

# Verify every XCFramework below the given roots. Fails when the tree contains
# none — an empty archive must never read as "everything passed".
#
# A no-op when signing is not configured, so unsigned local builds still run
# the same code path.
xcf_verify_tree() {
    if ! xcf_signing_configured && ! xcf_signing_required; then
        xcf_warn "skipping verification of: $* (no identity configured)"
        return 0
    fi

    local list status=0 count=0 xcf
    list=$(mktemp)
    xcf_find "$@" > "$list"
    while IFS= read -r -d '' xcf; do
        if ! xcf_verify_one "$xcf"; then status=1; break; fi
        count=$((count + 1))
    done < "$list"
    rm -f "$list"

    [ "$status" -eq 0 ] || return 1
    if [ "$count" -eq 0 ]; then
        xcf_err "no *.xcframework found under: $*"
        return 1
    fi
    xcf_log "verified $count xcframework(s) under: $*"
}
