#!/usr/bin/env python3
"""Validate target libc linkage without using the host's dynamic loader."""
from pathlib import Path
import json
import os
import re
import subprocess

root = Path(os.environ["CD_SYSROOT"])
tools = Path(os.environ["CD_TOOLS"])
work = Path(os.environ["CD_WORK_DIR"])
target = os.environ["CD_TARGET"]
compiler = str(tools / "bin" / f"{target}-gcc")
readelf = str(tools / "bin" / f"{target}-readelf")
source = work / "libc-probe.c"
source.write_text('#include <stdio.h>\n#include <gnu/libc-version.h>\n'
                  'int main(void) { printf("CUSTOM_LIBC_OK %s\\n", gnu_get_libc_version()); return 0; }\n')
binary = work / "libc-probe"
object_file = work / "libc-probe.o"
subprocess.run([compiler, "-c", str(source), "-o", str(object_file)], check=True)
compiled = subprocess.run([compiler, str(object_file), "-o", str(binary), "-v", "-Wl,--verbose"],
                          text=True, capture_output=True, check=True)
link_log = compiled.stdout + compiled.stderr
(work / "linkage.log").write_text(link_log)
headers = subprocess.run([compiler, "-E", "-v", "-x", "c", "-"], input="", text=True,
                         capture_output=True, check=True).stderr
search = headers.split("#include <...> search starts here:", 1)[1].split("End of search list.", 1)[0]
for line in search.splitlines():
    path = line.strip()
    if path and not (path.startswith(str(tools) + "/") or path.startswith(str(root) + "/")):
        raise SystemExit(f"Host header leakage: {path}")
if str(root / "usr/lib/libc.so.6") not in link_log:
    raise SystemExit("Linker did not use the source-built libc")
program_headers = subprocess.check_output([readelf, "-l", str(binary)], text=True)
if "[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]" not in program_headers:
    raise SystemExit("Unexpected target ELF interpreter")
for bad in ["RPATH", "RUNPATH"]:
    if bad in subprocess.check_output([readelf, "-d", str(binary)], text=True):
        raise SystemExit(f"Probe contains unexpected {bad}")
for line in link_log.splitlines():
    match = re.search(r"attempt to open (.+) succeeded", line)
    if match:
        path = match.group(1)
        if path.startswith("/") and path != str(object_file) and not (path.startswith(str(root) + "/") or path.startswith(str(tools) + "/")):
            raise SystemExit(f"Host library linkage: {path}")
loader = root / "usr/lib/ld-linux-x86-64.so.2"
result = subprocess.check_output([str(loader), "--library-path", str(root / "usr/lib"), str(binary)], text=True)
if result.strip() != "CUSTOM_LIBC_OK 2.42":
    raise SystemExit(f"Unexpected libc probe result: {result}")
report = {"target": target, "libc": "2.42", "sysroot": str(root), "compiler": compiler,
          "interpreter": "/lib64/ld-linux-x86-64.so.2", "probe_result": result.strip(),
          "header_search": search.strip().splitlines(), "linkage_log": str(work / "linkage.log"),
          "scope": "temporary cross toolchain; not a self-hosted final distribution"}
(root / ".bootstrap-validated.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
