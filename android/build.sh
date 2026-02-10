#!/bin/bash
set -eu -o pipefail

script_dir=$(dirname $(realpath $0))
version=${1:?}
abi=${2:?}
read version_major version_minor version_micro < <(
    echo $version | sed -E 's/^([0-9]+)\.([0-9]+)\.([0-9]+).*/\1 \2 \3/'
)
version_short=$version_major.$version_minor
version_no_pre=$version_major.$version_minor.$version_micro

if [ "$version_short" != "3.13" ]; then
    echo "This branch only supports Python 3.13.x for Android, got: $version"
    exit 1
fi

PREFIX="$script_dir/install/android/$abi/python-${version}"
mkdir -p "$PREFIX"
PREFIX=$(realpath "$PREFIX")

cd $script_dir
. abi-to-host.sh
: ${api_level:=24}

# Download and unpack Python source code.
version_dir=$script_dir/build/$version
mkdir -p $version_dir
cd $version_dir
src_filename=Python-$version.tgz
wget -c https://www.python.org/ftp/python/$version_no_pre/$src_filename

build_dir=$version_dir/$abi
rm -rf $build_dir
tar -xf "$src_filename"
mv "Python-$version" "$build_dir"
cd "$build_dir"

# Remove any existing installation in the prefix.
rm -rf $PREFIX/{include,lib}/python$version_short
rm -rf $PREFIX/lib/libpython$version_short*

# create VERSIONS support file
support_versions=$script_dir/support/$version_short/android/VERSIONS
mkdir -p $(dirname $support_versions)
echo ">>> Create VERSIONS file for android"
echo "Python version: $version" > $support_versions
echo "Build: 1" >> $support_versions
echo "Min android version: $api_level" >> $support_versions
echo "---------------------" >> $support_versions

case "$abi" in
    arm64-v8a|x86_64)
        ;;
    *)
        echo "Python $version_short official Android build supports only: arm64-v8a, x86_64"
        exit 1
        ;;
esac

# CPython's Android tooling expects ANDROID_HOME and ANDROID_API_LEVEL.
export ANDROID_API_LEVEL="$api_level"
if [ -z "${ANDROID_HOME:-}" ]; then
    if [ -d "$HOME/Library/Android/sdk" ]; then
        export ANDROID_HOME="$HOME/Library/Android/sdk"
    elif [ -d "$HOME/Android/Sdk" ]; then
        export ANDROID_HOME="$HOME/Android/Sdk"
    else
        export ANDROID_HOME="$script_dir/android-sdk"
        mkdir -p "$ANDROID_HOME"
    fi
fi

# Reuse NDK installed by this repo's older workflow by exposing it
# at the path expected by CPython's Android/android-env.sh.
if [ -z "${NDK_HOME:-}" ] && [ -d "$HOME/ndk/r29" ]; then
    export NDK_HOME="$HOME/ndk/r29"
fi
cpython_ndk_version=$(sed -n 's/^ndk_version=//p' Android/android-env.sh | head -n1)
if [ -n "${NDK_HOME:-}" ] && [ -d "$NDK_HOME" ] && [ -n "${cpython_ndk_version:-}" ]; then
    mkdir -p "$ANDROID_HOME/ndk"
    if [ ! -e "$ANDROID_HOME/ndk/$cpython_ndk_version" ]; then
        ln -s "$NDK_HOME" "$ANDROID_HOME/ndk/$cpython_ndk_version"
    fi
fi

Android/android.py configure-build
Android/android.py make-build
Android/android.py configure-host "$HOST"
Android/android.py make-host "$HOST"
cp -a "cross-build/$HOST/prefix/"* "$PREFIX"
