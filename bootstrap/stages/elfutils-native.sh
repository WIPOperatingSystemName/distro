#!/usr/bin/bash
set -euo pipefail
# A native generator dependency for Linux objtool, never a target runtime library.
# /usr/include/zlib.h and host libz are explicit development seed dependencies.
test -f /usr/include/zlib.h
cd "$CD_WORK_DIR"
"$CD_SOURCE_DIR/configure" --prefix="$CD_TOOLS/native" \
  --disable-debuginfod --disable-libdebuginfod --disable-nls \
  --without-bzlib --without-lzma --without-zstd
# Only libelf is needed by objtool; avoid building unrelated disassemblers/tools.
make -C lib -j"$CD_JOBS"
make -C libelf -j"$CD_JOBS"
make -C libelf install
install -Dm644 config/libelf.pc "$CD_TOOLS/native/lib/pkgconfig/libelf.pc"
