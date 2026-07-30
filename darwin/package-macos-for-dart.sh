#!/usr/bin/env bash
set -euo pipefail

python_apple_support_root=${1:?}
python_version=${2:?}

script_dir=$(dirname $(realpath $0))

# shellcheck source=darwin/xcframework_identifiers.sh
. "$script_dir/xcframework_identifiers.sh"

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
# Overlay the module map and public headers.
#
# macOS frameworks are VERSIONED bundles: everything at the framework root must be
# a symlink into Versions/Current, and only `Versions` itself is a real directory.
# These used to be written as real directories at the root, which codesign rejects
# outright once the framework is signed in its own right:
#
#   Python.framework: unsealed contents present in the root directory of an
#   embedded framework
#
# That went unnoticed while only the outer xcframework was signed. Write them into
# Versions/Current and symlink from the root, which is both what codesign requires
# and what CPython's own macOS framework does for Headers and Resources.
macos_fw=$frameworks_dir/Python.xcframework/macos-arm64_x86_64/Python.framework
[ -d "$macos_fw/Versions/Current" ] || {
    echo "expected a versioned macOS framework at $macos_fw"; exit 1; }

cp -r $script_dir/Modules "$macos_fw/Versions/Current/"
mkdir -p "$macos_fw/Versions/Current/Headers"
cp -r $python_apple_support_root/support/$python_version_short/macOS/Python.xcframework/macos-arm64_x86_64/Python.framework/Versions/$python_version_short/include/python$python_version_short/* "$macos_fw/Versions/Current/Headers"
# The built-in modulemap (if the source framework shipped one) is replaced by the
# overlaid darwin/Modules/module.modulemap above; -f tolerates builds without one.
rm -f "$macos_fw/Versions/Current/Headers/module.modulemap"

for _d in Headers Modules; do
    rm -rf "$macos_fw/$_d"
    ln -sfn "Versions/Current/$_d" "$macos_fw/$_d"
done

# Last mutation of the bundle: stable, provider-owned identifier for the Python
# runtime, replacing CPython's shared `org.python.python`. The macOS framework is
# versioned, so this writes Versions/<short>/Resources/Info.plist and leaves the
# nested Python.app's own identifier alone. Must precede signing — see
# xcframework_identifiers.sh.
xcf_set_framework_identifier "$frameworks_dir/Python.xcframework" "$XCF_PYTHON_RUNTIME_IDENTIFIER"
xcf_validate_identifiers "$frameworks_dir"

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
