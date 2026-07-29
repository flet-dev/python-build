#!/usr/bin/env bash
#
# Stable, provider-owned bundle identifiers for the XCFrameworks this repo
# publishes — and the validation that keeps them that way.
#
# WHY
#   A framework's CFBundleIdentifier becomes its code-signing identifier, and it
#   is baked in here, long before anyone knows which app will embed it. Two
#   properties matter:
#
#     * It must NOT depend on the consuming application's bundle id. Rewriting
#       it downstream (as serious_python used to) edits an Info.plist inside an
#       already-signed XCFramework and destroys the provider signature — the
#       exact thing the IPA's Signatures/ receipts report on.
#     * It must be unique per framework. `_ssl` and `_hashlib` shipping the same
#       identifier is the kind of collision that only shows up as a confusing
#       App Store validation error.
#
#   `dev.flet.python.*` is owned by the Flet publishing team, so one identifier
#   is correct in every app forever.
#
# NAMING
#   Module name with every character outside [A-Za-z0-9-] mapped to '-'. Leading
#   hyphens are KEPT: `_ssl` -> `dev.flet.python.-ssl`. That is what CPython's own
#   iOS support emits (Python.xcframework/build/utils.sh does `tr "_" "-"` and
#   nothing else) and what serious_python has been shipping; stripping the hyphen
#   would silently merge `_ssl` with a hypothetical `ssl`.
#
# CLI:
#   xcframework_identifiers.sh set <xcframework> <identifier>
#   xcframework_identifiers.sh validate <root>...

# Prefix every identifier this repo mints must start with. Asserted by
# xcf_validate_identifiers, which is what catches an app-scoped identifier
# sneaking back in.
XCF_IDENTIFIER_PREFIX=${XCF_IDENTIFIER_PREFIX:-dev.flet.}

# The Python runtime's stable identifier, shared by every slice of every
# Python.xcframework we publish (iOS and macOS).
XCF_PYTHON_RUNTIME_IDENTIFIER=${XCF_PYTHON_RUNTIME_IDENTIFIER:-dev.flet.python.runtime}

xcfid_err() { echo "xcframework-identifiers: ERROR: $*" >&2; }

# CFBundleIdentifier grammar: dot-separated, non-empty components of
# [A-Za-z0-9-].
xcf_identifier_is_valid() {
    printf '%s' "$1" | grep -Eq '^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*$'
}

# Turn an arbitrary module name into one identifier component.
xcf_identifier_component() {
    printf '%s' "$1" | tr '_' '-' | sed 's/[^A-Za-z0-9-]/-/g'
}

# Emit the path of every slice's OWN framework Info.plist inside an xcframework.
#
# Deliberately not a blanket `find -name Info.plist`: the macOS Python.framework
# carries a nested Python.app whose Info.plist must not be touched, and the
# xcframework's own root Info.plist is a manifest, not a bundle descriptor.
# Versions/Current is a symlink to the real version directory — skipped so each
# plist is visited once.
xcf_framework_plists() {
    local xcf=$1
    local name
    name=$(basename "$xcf" .xcframework)

    local slice p
    for slice in "$xcf"/*/; do
        [ -d "$slice$name.framework" ] || continue
        # Flat (iOS) layout.
        [ -f "$slice$name.framework/Info.plist" ] && printf '%s\n' "$slice$name.framework/Info.plist"
        # Versioned (macOS) layout.
        for p in "$slice$name.framework"/Versions/*/Resources/Info.plist; do
            [ -f "$p" ] || continue
            case "$p" in */Versions/Current/*) continue ;; esac
            printf '%s\n' "$p"
        done
    done
}

# Assign one identifier to every slice of an xcframework.
#
# MUST run before the xcframework is signed — a plist edit afterwards
# invalidates the seal.
xcf_set_framework_identifier() {
    local xcf=$1 identifier=$2

    [ -d "$xcf" ] || { xcfid_err "not a directory: $xcf"; return 1; }
    if ! xcf_identifier_is_valid "$identifier"; then
        xcfid_err "'$identifier' is not a valid CFBundleIdentifier"
        return 1
    fi

    local plists count=0 plist err
    plists=$(xcf_framework_plists "$xcf")
    while IFS= read -r plist; do
        [ -n "$plist" ] || continue
        if ! err=$(plutil -replace CFBundleIdentifier -string "$identifier" "$plist" 2>&1); then
            xcfid_err "plutil failed for $plist: $err"
            return 1
        fi
        count=$((count + 1))
    done <<EOF
$plists
EOF

    if [ "$count" -eq 0 ]; then
        xcfid_err "no framework Info.plist found in $xcf"
        return 1
    fi
    echo "xcframework-identifiers: $xcf -> $identifier ($count slice plist(s))"
}

# Validate every xcframework below the given roots:
#   * each slice's identifier is syntactically valid
#   * all slices of one xcframework agree (device and simulator must match, or
#     the two halves sign as different bundles)
#   * identifiers are unique across the whole set
#   * identifiers carry the provider prefix, i.e. do not depend on any app
xcf_validate_identifiers() {
    local list seen status=0 count=0 xcf name plists plist ident first
    list=$(mktemp)
    seen=$(mktemp)

    local root
    for root in "$@"; do
        [ -e "$root" ] || continue
        find "$root" -name '*.xcframework' -prune -print0
    done > "$list"

    while IFS= read -r -d '' xcf; do
        name=$(basename "$xcf" .xcframework)
        plists=$(xcf_framework_plists "$xcf")
        first=""
        local slice_count=0
        while IFS= read -r plist; do
            [ -n "$plist" ] || continue
            ident=$(plutil -extract CFBundleIdentifier raw -o - "$plist" 2>/dev/null || true)
            if [ -z "$ident" ]; then
                xcfid_err "$plist: no CFBundleIdentifier"
                status=1; continue
            fi
            if ! xcf_identifier_is_valid "$ident"; then
                xcfid_err "$plist: '$ident' is not a valid CFBundleIdentifier"
                status=1
            fi
            case "$ident" in
                "$XCF_IDENTIFIER_PREFIX"*) ;;
                *) xcfid_err "$plist: '$ident' does not start with '$XCF_IDENTIFIER_PREFIX';" \
                             "provider identifiers must not depend on the consuming app"
                   status=1 ;;
            esac
            if [ -z "$first" ]; then
                first=$ident
            elif [ "$ident" != "$first" ]; then
                xcfid_err "$xcf: slices disagree on CFBundleIdentifier ('$first' vs '$ident')"
                status=1
            fi
            slice_count=$((slice_count + 1))
        done <<EOF
$plists
EOF

        if [ "$slice_count" -eq 0 ]; then
            xcfid_err "$xcf: no $name.framework Info.plist in any slice"
            status=1
            continue
        fi

        if [ -n "$first" ]; then
            if grep -qxF "$first" "$seen"; then
                xcfid_err "$xcf: identifier '$first' is already used by another xcframework"
                status=1
            else
                printf '%s\n' "$first" >> "$seen"
            fi
        fi
        count=$((count + 1))
    done < "$list"

    rm -f "$list" "$seen"

    if [ "$count" -eq 0 ]; then
        xcfid_err "no *.xcframework found under: $*"
        return 1
    fi
    [ "$status" -eq 0 ] || return 1
    echo "xcframework-identifiers: validated $count xcframework(s) under: $*"
}

# CLI entry point when executed rather than sourced.
if [ "${BASH_SOURCE[0]:-}" = "$0" ]; then
    set -euo pipefail
    cmd=${1:?usage: xcframework_identifiers.sh set <xcframework> <identifier> | validate <root>...}
    shift
    case "$cmd" in
        set)      xcf_set_framework_identifier "$@" ;;
        validate) xcf_validate_identifiers "$@" ;;
        *)        echo "xcframework_identifiers.sh: unknown command '$cmd'" >&2; exit 2 ;;
    esac
fi
