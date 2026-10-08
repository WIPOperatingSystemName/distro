#!/usr/bin/env python3
"""Export the source-built C++ runtime and target C++ headers."""
from pathlib import Path
import hashlib
import json
import os
import shutil

if os.environ.get("CD_BUILD_MODE") != "target":
    raise SystemExit("libstdc++ requires the source-built second-pass target compiler")
prefix = Path(os.environ["CD_TOOLS"]) / "pass2"
target = os.environ["CD_TARGET"]
report = json.loads((prefix / ".runtime-validated.json").read_text())
if report["target"] != target or report["probe_result"] != "CUSTOM_CXX_OK":
    raise SystemExit("Second-pass C++ runtime validation does not match the target")
runtime = prefix / target / "lib"
with (runtime / "libstdc++.so.6").open("rb") as stream:
    if hashlib.file_digest(stream, "sha256").hexdigest() != report["sha256"]["libstdc++.so.6"]:
        raise SystemExit("C++ runtime changed since its execution probe")
stage = Path(os.environ["CD_STAGE_DIR"])
destination = stage / "usr/lib"
destination.mkdir(parents=True)
for pattern in ["libstdc++.so*", "libstdc++.a", "libstdc++exp.a", "libsupc++.a"]:
    for library in sorted(runtime.glob(pattern)):
        if library.is_symlink():
            (destination / library.name).symlink_to(os.readlink(library))
        else:
            shutil.copy2(library, destination / library.name)
headers = prefix / target / "include/c++"
if not (headers / "15.2.0").is_dir():
    raise SystemExit("The source-built target C++ development headers are absent")
shutil.copytree(headers, stage / "usr/include/c++", symlinks=True)
licenses = stage / "usr/share/licenses/libstdc++"
licenses.mkdir(parents=True)
source = Path(os.environ["CD_SOURCE_DIR"])
for name in ["COPYING3", "COPYING.RUNTIME"]:
    shutil.copy2(source / name, licenses / name)
