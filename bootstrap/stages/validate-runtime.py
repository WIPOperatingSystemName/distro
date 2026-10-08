#!/usr/bin/env python3
"""Check the independent pass2 C++ runtime without the host dynamic loader."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess

root = Path(os.environ["CD_SYSROOT"])
tools = Path(os.environ["CD_TOOLS"])
prefix = tools / "pass2"
work = Path(os.environ["CD_WORK_DIR"])
target = os.environ["CD_TARGET"]
compiler = prefix / "bin" / f"{target}-g++"
readelf = tools / "bin" / f"{target}-readelf"
runtime = prefix / target / "lib"
source = work / "cxx-probe.cc"
source.write_text('#include <iostream>\n#include <string>\n#include <stdexcept>\n'
                  'int main() { try { throw std::runtime_error("CUSTOM_CXX_OK"); } '
                  'catch (const std::exception &e) { std::cout << e.what() << std::endl; } }\n')
binary = work / "cxx-probe"
obj = work / "cxx-probe.o"
subprocess.run([str(compiler), f"--sysroot={root}", "-c", str(source), "-o", str(obj)], check=True)
compiled = subprocess.run([str(compiler), f"--sysroot={root}", str(obj), "-o", str(binary), "-v", "-Wl,--verbose"],
                          text=True, capture_output=True, check=True)
link_log = compiled.stdout + compiled.stderr
(work / "cxx-linkage.log").write_text(link_log)
for line in link_log.splitlines():
    match = re.search(r"attempt to open (.+) succeeded", line)
    if match:
        path = Path(match.group(1))
        resolved = path.resolve(strict=True)
        if path.is_absolute() and resolved != obj.resolve() and not (resolved.is_relative_to(root) or resolved.is_relative_to(tools)):
            raise SystemExit(f"C++ probe linked outside target inputs: {path}")
header_output = subprocess.run([str(compiler), f"--sysroot={root}", "-E", "-v", "-x", "c++", "-"],
                              input="", text=True, capture_output=True, check=True).stderr
search = header_output.split("#include <...> search starts here:", 1)[1].split("End of search list.", 1)[0]
for line in search.splitlines():
    if not line.strip():
        continue
    directory = Path(line.strip()).resolve(strict=True)
    if not (directory.is_relative_to(root) or directory.is_relative_to(tools)):
        raise SystemExit(f"C++ probe selected a host include directory: {directory}")
headers = subprocess.check_output([str(readelf), "-l", str(binary)], text=True)
dynamic = subprocess.check_output([str(readelf), "-d", str(binary)], text=True)
if "[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]" not in headers:
    raise SystemExit("C++ probe has the wrong target interpreter")
if any(option in dynamic for option in ["RPATH", "RUNPATH"]):
    raise SystemExit("C++ probe contains an unexpected runtime search path")
if not all(name in dynamic for name in ["libstdc++.so.6", "libgcc_s.so.1", "libc.so.6"]):
    raise SystemExit("C++ exception probe did not exercise the required runtime libraries")
allowed_libraries = {"libc.so.6", "libm.so.6", "libgcc_s.so.1", "ld-linux-x86-64.so.2"}
for library in ["libgcc_s.so.1", "libstdc++.so.6"]:
    dynamic_library = subprocess.check_output([str(readelf), "-d", str(runtime / library)], text=True)
    required = set(re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dynamic_library))
    if required - allowed_libraries or any(option in dynamic_library for option in ["RPATH", "RUNPATH"]):
        raise SystemExit(f"Unexpected target runtime linkage in {library}: {required}")
loader = root / "usr/lib/ld-linux-x86-64.so.2"
result = subprocess.check_output([str(loader), "--library-path", f"{runtime}:{root / 'usr/lib'}", str(binary)], text=True)
if result.strip() != "CUSTOM_CXX_OK":
    raise SystemExit(f"C++ runtime probe failed: {result}")
report = {"target": target, "compiler": str(compiler), "sysroot": str(root),
          "runtime": str(runtime), "probe_result": result.strip(),
          "header_search": search.strip().splitlines(),
          "libraries": ["libgcc_s.so.1", "libstdc++.so.6"],
          "scope": "native-executing cross C/C++ compiler; target runtime; not self-hosted"}
report["sha256"] = {}
for library in ["libgcc_s.so.1", "libstdc++.so.6"]:
    with (runtime / library).open("rb") as stream:
        report["sha256"][library] = hashlib.file_digest(stream, "sha256").hexdigest()
(prefix / ".runtime-validated.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
