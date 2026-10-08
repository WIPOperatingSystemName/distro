#!/usr/bin/bash
set -euo pipefail
umask 022

# Native code generator only; its host runtime is never copied to the OS.
"$CD_SOURCE_DIR/configure" --prefix="$CD_TOOLS/native" --disable-nls
make -j"$CD_JOBS"
make install
"$CD_TOOLS/native/bin/gperf" --version
