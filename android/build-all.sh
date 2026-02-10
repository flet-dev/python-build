#!/usr/bin/env bash
set -euo pipefail

python_version=${1:?}
abis="arm64-v8a x86_64"

for abi in $abis; do
    bash ./build.sh $python_version $abi
done
