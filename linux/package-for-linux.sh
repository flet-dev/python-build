#!/usr/bin/env bash
set -euo pipefail

PYTHON_ARCH=${1:?}
PYTHON_ARCH_VER=${2:-""}

if [ -z "${PYTHON_VERSION:-}" ]; then
    echo "PYTHON_VERSION is required (e.g. 3.13.12)"
    exit 1
fi
if [ -z "${PYTHON_DIST_RELEASE:-}" ]; then
    echo "PYTHON_DIST_RELEASE is required"
    exit 1
fi

PYTHON_VERSION_SHORT=${PYTHON_VERSION_SHORT:-$(echo "$PYTHON_VERSION" | cut -d. -f1,2)}

DIST_FILE=cpython-${PYTHON_VERSION}+${PYTHON_DIST_RELEASE}-${PYTHON_ARCH}${PYTHON_ARCH_VER}-unknown-linux-gnu-install_only_stripped.tar.gz
curl -OL https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_DIST_RELEASE}/${DIST_FILE}
mkdir -p $PYTHON_ARCH/build
tar zxvf $DIST_FILE -C $PYTHON_ARCH/build

# compile lib
build_python=$(command -v "python$PYTHON_VERSION_SHORT" || true)
if [ -z "$build_python" ]; then
    build_python=$(command -v python3 || true)
fi
if [ -z "$build_python" ]; then
    build_python=$(command -v python || true)
fi
if [ -z "$build_python" ]; then
    echo "Host Python interpreter not found for compileall"
    exit 1
fi
"$build_python" -I -m compileall -b "$PYTHON_ARCH/build/python/lib/python$PYTHON_VERSION_SHORT"

# copy build to dist
mkdir -p $PYTHON_ARCH/dist
rsync -av --exclude-from=python-linux-dart.exclude $PYTHON_ARCH/build/python/* $PYTHON_ARCH/dist

# archive
tar -czf "python-linux-dart-$PYTHON_VERSION_SHORT-$PYTHON_ARCH.tar.gz" -C "$PYTHON_ARCH/dist" .
