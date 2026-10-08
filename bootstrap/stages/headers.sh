#!/usr/bin/env bash
set -euo pipefail
umask 022

make -C "$CD_SOURCE_DIR" O="$CD_WORK_DIR" ARCH=x86_64 headers
mkdir -p "$CD_SYSROOT/usr/include"
python3 - <<'PY'
from pathlib import Path
import os, shutil
source = Path(os.environ['CD_WORK_DIR']) / 'usr/include'
destination = Path(os.environ['CD_SYSROOT']) / 'usr/include'
for header in source.rglob('*.h'):
    target = destination / header.relative_to(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(header, target)
PY
