#!/usr/bin/env bash
set -euo pipefail
umask 022

"$CD_SOURCE_DIR/configure" \
    --prefix="$CD_TOOLS" \
    --with-sysroot="$CD_SYSROOT" \
    --target="$CD_TARGET" \
    --disable-nls \
    --enable-gprofng=no \
    --disable-werror \
    --enable-new-dtags \
    --enable-default-hash-style=gnu \
    --enable-deterministic-archives
make -j"$CD_JOBS"
make install
"$CD_TOOLS/bin/$CD_TARGET-ld" --version
