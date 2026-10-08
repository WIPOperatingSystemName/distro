#!/usr/bin/env bash
set -euo pipefail
umask 022

mkdir -p "$CD_SYSROOT/usr/lib" "$CD_SYSROOT/usr/bin" "$CD_SYSROOT/lib64"
ln -sfn usr/lib "$CD_SYSROOT/lib"
ln -sfn ../usr/lib/ld-linux-x86-64.so.2 "$CD_SYSROOT/lib64/ld-linux-x86-64.so.2"
ln -sfn ld-linux-x86-64.so.2 "$CD_SYSROOT/lib64/ld-lsb-x86-64.so.3"
printf '%s\n' 'rootsbindir=/usr/sbin' > configparms
build_triplet="$($CD_SOURCE_DIR/scripts/config.guess)"
CC="$CD_TOOLS/bin/$CD_TARGET-gcc" \
CXX=false \
TEST_CXX=false \
libc_cv_cxx_link_ok=no \
AR="$CD_TOOLS/bin/$CD_TARGET-ar" \
RANLIB="$CD_TOOLS/bin/$CD_TARGET-ranlib" \
"$CD_SOURCE_DIR/configure" \
    --prefix=/usr \
    --host="$CD_TARGET" \
    --build="$build_triplet" \
    --with-headers="$CD_SYSROOT/usr/include" \
    --enable-kernel=5.4 \
    --disable-nscd \
    --disable-werror \
    libc_cv_slibdir=/usr/lib
make -j"$CD_JOBS"
make DESTDIR="$CD_SYSROOT" install
python3 - <<'PY'
from pathlib import Path
import os
script = Path(os.environ['CD_SYSROOT']) / 'usr/bin/ldd'
text = script.read_text()
script.write_text('\n'.join(line.replace('/usr/', '/') if line.startswith('RTLDLIST=') else line
                             for line in text.splitlines()) + '\n')
PY
