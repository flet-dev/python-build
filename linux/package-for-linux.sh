#!/usr/bin/env bash
set -euo pipefail

# $1: PBS arch (x86_64, aarch64). $2: microarch suffix (e.g. _v2 for x86_64 — a broad
# baseline covering ~all x86-64 CPUs since ~2009; aarch64 has no such variants so "").
PYTHON_ARCH=${1:?}
PYTHON_ARCH_VER=${2:-""}

script_dir=$(dirname "$(realpath "$0")")

if [ -z "${PYTHON_VERSION:-}" ]; then
    echo "PYTHON_VERSION is required (e.g. 3.13.13)"
    exit 1
fi

PYTHON_VERSION_SHORT=${PYTHON_VERSION_SHORT:-$(echo "$PYTHON_VERSION" | cut -d. -f1,2)}

# Resolve the python-build-standalone download URL. PYTHON_DIST_RELEASE is optional:
# when set it pins a specific release date, otherwise the newest release shipping this
# exact micro version + arch is auto-resolved.
DIST_URL=$(python3 "$script_dir/resolve_pbs.py" \
    --version "$PYTHON_VERSION" \
    --arch "$PYTHON_ARCH" \
    --arch-ver "$PYTHON_ARCH_VER" \
    ${PYTHON_DIST_RELEASE:+--release "$PYTHON_DIST_RELEASE"})
DIST_FILE=$(basename "$DIST_URL")
echo "Downloading $DIST_FILE"
curl -OL "$DIST_URL"
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

# archive — filename uses the FULL python version (e.g. 3.14.6, not 3.14)
# so a single date-keyed release can host multiple patches of the same
# minor side by side.
tar -czf "python-linux-dart-$PYTHON_VERSION-$PYTHON_ARCH.tar.gz" -C "$PYTHON_ARCH/dist" .
