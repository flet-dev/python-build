#!/usr/bin/env bash
set -euo pipefail

python_apple_support_root=${1:?}
python_version=${2:?}

script_dir=$(dirname $(realpath $0))

# build short Python version
read python_version_major python_version_minor < <(echo $python_version | sed -E 's/^([0-9]+)\.([0-9]+).*/\1 \2/')
python_version_short=$python_version_major.$python_version_minor
python_bin=$(command -v "python$python_version_short" || true)
if [ -z "$python_bin" ]; then
    echo "python$python_version_short is required to compile stdlib bytecode"
    exit 1
fi

# create build directory
build_dir=build/python-$python_version
rm -rf $build_dir
mkdir -p $build_dir
build_dir=$(realpath $build_dir)

# create dist directory
mkdir -p dist

frameworks_dir=$build_dir/xcframeworks
stdlib_dir=$build_dir/python-stdlib
mkdir -p $frameworks_dir
mkdir -p $stdlib_dir

# copy Python.xcframework
rsync -av --exclude-from=$script_dir/python-darwin-framework.exclude $python_apple_support_root/support/$python_version_short/macOS/Python.xcframework $frameworks_dir
cp -r $script_dir/Modules $frameworks_dir/Python.xcframework/macos-arm64_x86_64/Python.framework
mkdir -p $frameworks_dir/Python.xcframework/macos-arm64_x86_64/Python.framework/Headers
cp -r $python_apple_support_root/support/$python_version_short/macOS/Python.xcframework/macos-arm64_x86_64/Python.framework/Versions/$python_version_short/include/python$python_version_short/* $frameworks_dir/Python.xcframework/macos-arm64_x86_64/Python.framework/Headers
rm $frameworks_dir/Python.xcframework/macos-arm64_x86_64/Python.framework/Headers/module.modulemap

# copy stdlibs
rsync -av --exclude-from=$script_dir/python-darwin-stdlib.exclude $python_apple_support_root/install/macOS/macosx/python-*/Python.framework/Versions/Current/lib/python$python_version_short/* $stdlib_dir

# compile stdlib with an isolated interpreter, without importing from target stdlib dir.
"$python_bin" -I -m compileall -b "$stdlib_dir"
cd $stdlib_dir
find . \( -name '*.py' -or -name '*.typed' \) -type f -delete
rm -rf __pycache__
rm -rf **/__pycache__
cd -

# final archive
tar -czf dist/python-macos-dart-$python_version.tar.gz -C $build_dir .
