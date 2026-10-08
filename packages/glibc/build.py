#!/usr/bin/env python3
"""Export the audited source-built bootstrap libc; never copy host libraries."""
from pathlib import Path
import json
import os
import shutil

if os.environ.get("CD_BUILD_MODE") != "target":
    raise SystemExit("glibc runtime requires the source-built target bootstrap")
root = Path(os.environ["CD_SYSROOT"])
source = Path(os.environ["CD_SOURCE_DIR"])
stage = Path(os.environ["CD_STAGE_DIR"])
report_path = root / ".bootstrap-validated.json"
if not report_path.is_file():
    raise SystemExit("Run every bootstrap stage including target linkage validation first")
report = json.loads(report_path.read_text())
if report["libc"] != "2.42" or report["target"] != os.environ["CD_TARGET"]:
    raise SystemExit("Bootstrap libc/target does not match this recipe")
if not (root / "usr/lib/libc.a").is_file():
    raise SystemExit("Bootstrap source-built static libc is absent")
shutil.copytree(root / "usr/lib", stage / "usr/lib", symlinks=True)
(stage / "lib").symlink_to("usr/lib")
(stage / "lib64").mkdir()
(stage / "lib64/ld-linux-x86-64.so.2").symlink_to("../usr/lib/ld-linux-x86-64.so.2")
(stage / "lib64/ld-lsb-x86-64.so.3").symlink_to("ld-linux-x86-64.so.2")
licenses = stage / "usr/share/licenses/glibc"
licenses.mkdir(parents=True)
shutil.copy2(source / "COPYING.LIB", licenses / "COPYING.LIB")
