#!/bin/bash
set -eu

python_version=${1:?}
abi=${2:?}
NDK_VERSION=r27
api_level=24

bzip2_version=1.0.8-1
xz_version=5.4.6-0
libffi_version=3.4.4-2
openssl_version=3.0.15-0
sqlite_version=3.45.2-0

os=android
build=custom

project_dir=$(dirname $(realpath $0))
downloads=$project_dir/downloads

# build short Python version
read python_version_major python_version_minor < <(echo $python_version | sed -E 's/^([0-9]+)\.([0-9]+).*/\1 \2/')
if [[ $python_version =~ ^[0-9]+\.[0-9]+$ ]]; then
    python_version=$(curl --silent "https://www.python.org/ftp/python/" | sed -nr "s/^.*\"($python_version_major\.$python_version_minor\.[0-9]+)\/\".*$/\1/p" | sort -rV | head -n 1)
    echo "Python version: $python_version"
fi
python_version_short=$python_version_major.$python_version_minor
python_version_int=$(($python_version_major * 100 + $python_version_minor))

curl_flags="--disable --fail --location --create-dirs --progress-bar"
mkdir -p $downloads

case $abi in
    armeabi-v7a)
        host_triplet=arm-linux-androideabi
        ;;
    arm64-v8a)
        host_triplet=aarch64-linux-android
        ;;
    x86)
        host_triplet=i686-linux-android
        ;;
    x86_64)
        host_triplet=x86_64-linux-android
        ;;
    *)
        fail "Unknown ABI: '$abi'"
        ;;
esac

# create VERSIONS support file
support_versions=$project_dir/support/$python_version_short/$os/VERSIONS
mkdir -p $(dirname $support_versions)
echo ">>> Create VERSIONS file for $os"
echo "Python version: $python_version " > $support_versions
echo "Build: $build" >> $support_versions
echo "Min $os version: $api_level" >> $support_versions
echo "---------------------" >> $support_versions
echo "libFFI: $libffi_version" >> $support_versions
echo "BZip2: $bzip2_version" >> $support_versions
echo "OpenSSL: $openssl_version" >> $support_versions
echo "XZ: $xz_version" >> $support_versions

#      BZip2
# ===============
bzip2_install=$project_dir/install/$os/$abi/bzip2-$bzip2_version
bzip2_lib=$bzip2_install/lib/libbz2.a
bzip2_filename=bzip2-$bzip2_version-$host_triplet.tar.gz

echo ">>> Download BZip2 for $abi"
curl $curl_flags -o $downloads/$bzip2_filename \
    https://github.com/beeware/cpython-android-source-deps/releases/download/bzip2-$bzip2_version/$bzip2_filename

echo ">>> Install BZip2 for $abi"
mkdir -p $bzip2_install
tar zxvf $downloads/$bzip2_filename -C $bzip2_install
touch $bzip2_lib

#      XZ (LZMA)
# =================
xz_install=$project_dir/install/$os/$abi/xz-$xz_version
xz_lib=$xz_install/lib/liblzma.a
xz_filename=xz-$xz_version-$host_triplet.tar.gz

echo ">>> Download XZ for $abi"
curl $curl_flags -o $downloads/$xz_filename \
    https://github.com/beeware/cpython-android-source-deps/releases/download/xz-$xz_version/$xz_filename

echo ">>> Install XZ for $abi"
mkdir -p $xz_install
tar zxvf $downloads/$xz_filename -C $xz_install
touch $xz_lib

#      LibFFI
# =================
libffi_install=$project_dir/install/$os/$abi/libffi-$libffi_version
libffi_lib=$libffi_install/lib/libffi.a
libffi_filename=libffi-$libffi_version-$host_triplet.tar.gz

echo ">>> Download LibFFI for $abi"
curl $curl_flags -o $downloads/$libffi_filename \
    https://github.com/beeware/cpython-android-source-deps/releases/download/libffi-$libffi_version/$libffi_filename

echo ">>> Install LibFFI for $abi"
mkdir -p $libffi_install
tar zxvf $downloads/$libffi_filename -C $libffi_install
touch $libffi_lib

#      OpenSSL
# =================
openssl_install=$project_dir/install/$os/$abi/openssl-$openssl_version
openssl_lib=$openssl_install/lib/libssl.a
openssl_filename=openssl-$openssl_version-$host_triplet.tar.gz

echo ">>> Download OpenSSL for $abi"
curl $curl_flags -o $downloads/$openssl_filename \
    https://github.com/beeware/cpython-android-source-deps/releases/download/openssl-$openssl_version/$openssl_filename

echo ">>> Install OpenSSL for $abi"
mkdir -p $openssl_install
tar zxvf $downloads/$openssl_filename -C $openssl_install
touch $openssl_lib

#      SQLite
# =================
sqlite_install=$project_dir/install/$os/$abi/sqlite-$sqlite_version
sqlite_lib=$sqlite_install/lib/libsqlite3.la
sqlite_filename=sqlite-$sqlite_version-$host_triplet.tar.gz

echo ">>> Download SQLite for $abi"
curl $curl_flags -o $downloads/$sqlite_filename \
    https://github.com/beeware/cpython-android-source-deps/releases/download/sqlite-$sqlite_version/$sqlite_filename

echo ">>> Install SQLite for $abi"
mkdir -p $sqlite_install
tar zxvf $downloads/$sqlite_filename -C $sqlite_install
touch $sqlite_lib

#      Python
# ===============

build_dir=$project_dir/build/$os/$abi
python_build_dir=$project_dir/build/$os/$abi/python-$python_version
python_install=$project_dir/install/$os/$abi/python-$python_version
python_lib=$sqlite_install/lib/libpython$python_version_short.a
python_filename=Python-$python_version.tgz

echo ">>> Download Python for $abi"
curl $curl_flags -o $downloads/$python_filename \
    https://www.python.org/ftp/python/$python_version/$python_filename

echo ">>> Unpack Python for $abi"
rm -rf $build_dir
mkdir -p $build_dir
tar zxvf $downloads/$python_filename -C $build_dir
mv $build_dir/Python-$python_version $python_build_dir
touch $python_build_dir/configure

echo ">>> Configuring Python build environment for $abi"

# configure build environment
prefix=$python_build_dir
. $project_dir/android-env.sh

cd $python_build_dir

# apply patches
echo ">>> Patching Python for $abi"
patches="dynload_shlib lfs soname"
if [ $python_version_int -le 311 ]; then
    patches+=" sysroot_paths"
fi
if [ $python_version_int -ge 311 ]; then
    patches+=" python_for_build_deps"
fi
if [ $python_version_int -ge 312 ]; then
    patches+=" bldlibrary grp"
fi
for name in $patches; do
    patch -p1 -i $project_dir/patches/$name.patch
done

# Add sysroot paths, otherwise Python 3.8's setup.py will think libz is unavailable.
CFLAGS+=" -I$toolchain/sysroot/usr/include"
LDFLAGS+=" -L$toolchain/sysroot/usr/lib/$host_triplet/$api_level"

# The configure script omits -fPIC on Android, because it was unnecessary on older versions of
# the NDK (https://bugs.python.org/issue26851). But it's definitely necessary on the current
# version, otherwise we get linker errors like "Parser/myreadline.o: relocation R_386_GOTOFF
# against preemptible symbol PyOS_InputHook cannot be used when making a shared object".
export CCSHARED="-fPIC"

# Override some tests.
cat > config.site <<EOF
# Things that can't be autodetected when cross-compiling.
ac_cv_aligned_required=no  # Default of "yes" changes hash function to FNV, which breaks Numba.
ac_cv_file__dev_ptmx=no
ac_cv_file__dev_ptc=no
EOF
export CONFIG_SITE=$(pwd)/config.site

echo ">>> Configuring Python for $abi"
./configure \
    LIBLZMA_CFLAGS="-I$xz_install/include" \
    LIBLZMA_LIBS="-L$xz_install/lib -llzma" \
    BZIP2_CFLAGS="-I$bzip2_install/include" \
    BZIP2_LIBS="-L$bzip2_install/lib -lbz2" \
    LIBFFI_CFLAGS="-I$libffi_install/include" \
    LIBFFI_LIBS="-L$libffi_install/lib -lffi" \
    LIBSQLITE3_CFLAGS="-I$sqlite_install/include" \
    LIBSQLITE3_LIBS="-L$sqlite_install/lib" \
    --host=$host_triplet \
    --build=$(./config.guess) \
    --with-build-python=yes \
    --prefix="$python_install" \
    --enable-ipv6 \
    --with-openssl="$openssl_install" \
    --enable-shared \
    --without-ensurepip \
	2>&1 | tee -a ../python-$python_version.config.log

echo ">>> Building Python for $abi"
make all \
    2>&1 | tee -a ../python-$python_version.build.log

echo ">>> Installing Python for $abi"
make install \
    2>&1 | tee -a ../python-$python_version.install.log

echo ">>> Copying Python dependencies $abi"
cp {$openssl_install,$sqlite_install}/lib/*_python.so $python_install/lib

echo ">>> Copying libc++_shared.so"
cp $LIBC_SHARED_SO $python_install/lib

echo ">>> Stripping dynamic libraries for $abi"
find $python_install -type f -iname "*.so" -exec $STRIP --strip-unneeded {} \;

echo ">>> Replacing host platform"
sed -i -e "s/_PYTHON_HOST_PLATFORM=.*/_PYTHON_HOST_PLATFORM=android-$api_level-$abi/" $python_install/lib/python$python_version_short/config-$python_version_short/Makefile

# the end!