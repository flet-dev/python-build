#!/bin/bash
set -eu

python_version=${1:?}
abis="arm64-v8a armeabi-v7a x86_64 x86"

for abi in $abis; do
    ./build.sh $python_version $abi
done