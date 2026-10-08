#!/usr/bin/bash
set -euo pipefail
umask 022

# Keep all stage1 compiler and libc outputs unchanged during this second pass.
test -f "$CD_SYSROOT/.bootstrap-validated.json"
python3 - <<'PY'
from pathlib import Path
import os, shutil
source = Path(os.environ['CD_SOURCE_DIR'])
inputs = Path(os.environ['CD_SOURCES_DIR'])
for name in ['gmp', 'mpfr', 'mpc']:
    dependency = next(p for p in (inputs / name).iterdir() if p.is_dir())
    destination = source / name
    # An interrupted stage can resume the same verified build tree. Replacing
    # these unchanged bundled sources would needlessly invalidate their objects.
    if not destination.exists():
        shutil.copytree(dependency, destination, symlinks=True)
config = source / 'gcc/config/i386/t-linux64'
text = config.read_text()
config.write_text('\n'.join(line.replace('lib64', 'lib') if 'm64=' in line else line
                             for line in text.splitlines()) + '\n')
PY

"$CD_SOURCE_DIR/configure" \
  --target="$CD_TARGET" \
  --prefix="$CD_TOOLS/pass2" \
  --with-sysroot="$CD_SYSROOT" \
  --with-build-sysroot="$CD_SYSROOT" \
  --with-as="$CD_TOOLS/bin/$CD_TARGET-as" \
  --with-ld="$CD_TOOLS/bin/$CD_TARGET-ld" \
  --enable-languages=c,c++ \
  --enable-shared \
  --enable-threads=posix \
  --enable-default-pie \
  --enable-default-ssp \
  --disable-bootstrap \
  --disable-nls \
  --disable-multilib \
  --disable-libsanitizer \
  --disable-libquadmath \
  --disable-libgomp \
  --disable-libitm \
  --disable-libssp \
  --disable-libvtv
make -j"$CD_JOBS"
make install
python3 "$(dirname "$0")/validate-runtime.py"
