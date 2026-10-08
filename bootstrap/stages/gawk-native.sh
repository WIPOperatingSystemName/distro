#!/usr/bin/env bash
set -euo pipefail
umask 022

# This executable runs on the build host; it is never copied into the target OS.
"$CD_SOURCE_DIR/configure" --prefix="$CD_TOOLS/native" --without-readline --without-mpfr
make -j"$CD_JOBS"
make install
ln -sfn gawk "$CD_TOOLS/native/bin/awk"
"$CD_TOOLS/native/bin/gawk" --version
