#!/usr/bin/env bash
set -euo pipefail
umask 022

# Linux's configuration generators use this native executable; it is not target payload.
"$CD_SOURCE_DIR/configure" --prefix="$CD_TOOLS/native" --disable-nls --disable-static
make -j"$CD_JOBS"
make install
"$CD_TOOLS/native/bin/flex" --version
