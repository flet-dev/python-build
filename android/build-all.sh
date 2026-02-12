#!/usr/bin/env bash
set -euo pipefail

python_version=${1:?}
read version_major version_minor < <(
    echo "$python_version" | sed -E 's/^([0-9]+)\.([0-9]+).*/\1 \2/'
)
version_int=$((version_major * 100 + version_minor))

if [ $version_int -ge 313 ]; then
    abis="arm64-v8a x86_64"
else
    abis="arm64-v8a armeabi-v7a x86_64 x86"
fi

for abi in $abis; do
    bash ./build.sh $python_version $abi
done
