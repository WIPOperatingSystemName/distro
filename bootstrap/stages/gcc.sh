#!/usr/bin/env bash
set -euo pipefail
umask 022

python3 - <<'PY'
from pathlib import Path
import os, shutil
source = Path(os.environ['CD_SOURCE_DIR'])
inputs = Path(os.environ['CD_SOURCES_DIR'])
for name in ['gmp', 'mpfr', 'mpc']:
    dependency = next(p for p in (inputs / name).iterdir() if p.is_dir())
    destination = source / name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(dependency, destination, symlinks=True)
config = source / 'gcc/config/i386/t-linux64'
text = config.read_text()
config.write_text('\n'.join(line.replace('lib64', 'lib') if 'm64=' in line else line
                             for line in text.splitlines()) + '\n')
PY

"$CD_SOURCE_DIR/configure" \
    --target="$CD_TARGET" \
    --prefix="$CD_TOOLS" \
    --with-glibc-version=2.42 \
    --with-sysroot="$CD_SYSROOT" \
    --with-newlib \
    --without-headers \
    --enable-default-pie \
    --enable-default-ssp \
    --disable-nls \
    --disable-shared \
    --disable-multilib \
    --disable-threads \
    --disable-libatomic \
    --disable-libgomp \
    --disable-libquadmath \
    --disable-libssp \
    --disable-libvtv \
    --disable-libstdcxx \
    --enable-languages=c
make -j"$CD_JOBS"
make install
python3 - <<'PY'
from pathlib import Path
import os, subprocess
source = Path(os.environ['CD_SOURCE_DIR'])
compiler = str(Path(os.environ['CD_TOOLS']) / 'bin' / (os.environ['CD_TARGET'] + '-gcc'))
libgcc = Path(subprocess.check_output([compiler, '-print-libgcc-file-name'], text=True).strip())
header = libgcc.parent / 'include/limits.h'
header.write_bytes(b''.join((source / 'gcc' / name).read_bytes()
                            for name in ['limitx.h', 'glimits.h', 'limity.h']))
PY
"$CD_TOOLS/bin/$CD_TARGET-gcc" --version
