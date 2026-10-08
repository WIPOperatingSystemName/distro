#!/usr/bin/env python3
"""Export shared compiler runtime from the validated source-built second pass."""
from pathlib import Path
import hashlib
import json
import os
import shutil

if os.environ.get("CD_BUILD_MODE") != "target":
    raise SystemExit("libgcc requires the source-built second-pass target compiler")
prefix = Path(os.environ["CD_TOOLS"]) / "pass2"
target = os.environ["CD_TARGET"]
report = json.loads((prefix / ".runtime-validated.json").read_text())
if report["target"] != target or report["probe_result"] != "CUSTOM_CXX_OK":
    raise SystemExit("Second-pass runtime validation does not match the target")
runtime = prefix / target / "lib"
with (runtime / "libgcc_s.so.1").open("rb") as stream:
    if hashlib.file_digest(stream, "sha256").hexdigest() != report["sha256"]["libgcc_s.so.1"]:
        raise SystemExit("Shared GCC runtime changed since its execution probe")
stage = Path(os.environ["CD_STAGE_DIR"])
destination = stage / "usr/lib"
destination.mkdir(parents=True)
for library in sorted(runtime.glob("libgcc_s.so*")):
    if library.is_symlink():
        (destination / library.name).symlink_to(os.readlink(library))
    else:
        shutil.copy2(library, destination / library.name)
licenses = stage / "usr/share/licenses/libgcc"
licenses.mkdir(parents=True)
source = Path(os.environ["CD_SOURCE_DIR"])
for name in ["COPYING3", "COPYING.RUNTIME"]:
    shutil.copy2(source / name, licenses / name)
