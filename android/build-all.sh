#!/usr/bin/env bash
set -euo pipefail

python_version=${1:?}
read version_major version_minor < <(
    echo "$python_version" | sed -E 's/^([0-9]+)\.([0-9]+).*/\1 \2/'
)
short="$version_major.$version_minor"

# ABIs come from the manifest (`pythons.<short>.android_abis`), the same source
# the CI packaging step reads — keep them in lockstep so a minor's ABI set is
# edited in exactly one place.
manifest="$(dirname "$(realpath "$0")")/../manifest.json"
abis=$(jq -r --arg s "$short" '.pythons[$s].android_abis[]' "$manifest")
if [ -z "$abis" ]; then
    echo "manifest.json has no .pythons[\"$short\"].android_abis" >&2
    exit 1
fi

for abi in $abis; do
    bash ./build.sh "$python_version" "$abi"
done
