fail() {
    echo "$1" >&2
    exit 1
}

if [[ -z "${NDK_HOME-}" ]]; then
    NDK_HOME=$HOME/ndk/$NDK_VERSION
    echo "NDK_HOME environment variable is not set."
    if [ ! -d $NDK_HOME ]; then
        echo "Installing NDK $NDK_VERSION to $NDK_HOME"

        if [ $(uname) = "Darwin" ]; then
            seven_zip=$downloads/7zip/7zz
            if ! test -f $seven_zip; then
                echo "Installing 7-zip"
                mkdir -p $(dirname $seven_zip)
                cd $(dirname $seven_zip)
                curl -#OL https://www.7-zip.org/a/7z2301-mac.tar.xz
                tar -xf 7z2301-mac.tar.xz
                cd -
            fi

            ndk_dmg=android-ndk-$NDK_VERSION-darwin.dmg
            if ! test -f $downloads/$ndk_dmg; then
                echo ">>> Downloading $ndk_dmg"
                curl -#L -o $downloads/$ndk_dmg https://dl.google.com/android/repository/$ndk_dmg
            fi

            cd $downloads
            $seven_zip x -snld $ndk_dmg
            mkdir -p $(dirname $NDK_HOME)
            mv Android\ NDK\ */AndroidNDK*.app/Contents/NDK $NDK_HOME
            rm -rf Android\ NDK\ *
            cd -
        else
            ndk_zip=android-ndk-$NDK_VERSION-linux.zip
            if ! test -f $downloads/$ndk_zip; then
                echo ">>> Downloading $ndk_zip"
                curl -#L -o $downloads/$ndk_zip https://dl.google.com/android/repository/$ndk_zip
            fi
            cd $downloads
            unzip -oq $ndk_zip
            mkdir -p $(dirname $NDK_HOME)
            mv android-ndk-$NDK_VERSION $NDK_HOME
            cd -
            echo "NDK installed to $NDK_HOME"
        fi
    else
        echo "NDK $NDK_VERSION is already installed in $NDK_HOME"
    fi
else
    echo "NDK home: $NDK_HOME"
fi

if [ $host_triplet = "arm-linux-androideabi" ]; then
    clang_triplet=armv7a-linux-androideabi
else
    clang_triplet=$host_triplet
fi

# These variables are based on BuildSystemMaintainers.md above, and
# $NDK_HOME/build/cmake/android.toolchain.cmake.
toolchain=$(echo $NDK_HOME/toolchains/llvm/prebuilt/*)
export AR="$toolchain/bin/llvm-ar"
export AS="$toolchain/bin/llvm-as"
export CC="$toolchain/bin/${clang_triplet}$api_level-clang"
export CXX="${CC}++"
export LD="$toolchain/bin/ld"
export NM="$toolchain/bin/llvm-nm"
export RANLIB="$toolchain/bin/llvm-ranlib"
export READELF="$toolchain/bin/llvm-readelf"
export STRIP="$toolchain/bin/llvm-strip"

# The quotes make sure the wildcard in the `toolchain` assignment has been expanded.
for path in "$AR" "$AS" "$CC" "$CXX" "$LD" "$NM" "$RANLIB" "$READELF" "$STRIP"; do
    if ! [ -e "$path" ]; then
        fail "$path does not exist"
    fi
done

# Use -idirafter so that package-specified -I directories take priority. For example,
# grpcio provides its own BoringSSL headers which must be used rather than our OpenSSL.
export CFLAGS="-idirafter ${prefix:?}/include"
export LDFLAGS="-L${prefix:?}/lib -Wl,--build-id=sha1 -Wl,--no-rosegment"

# Many packages get away with omitting this on standard Linux, but Android is stricter.
LDFLAGS+=" -lm"

case $abi in
    armeabi-v7a)
        CFLAGS+=" -march=armv7-a -mthumb"
        ;;
    x86)
        # -mstackrealign is unnecessary because it's included in the clang launcher script
        # which is pointed to by $CC.
        ;;
esac

export PKG_CONFIG="pkg-config --define-prefix"
export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"

# conda-build variable name
if [ $(uname) = "Darwin" ]; then
    export CPU_COUNT=$(sysctl -n hw.ncpu)
else
    export CPU_COUNT=$(nproc)
fi
