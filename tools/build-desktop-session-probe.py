#!/usr/bin/env python3
"""Build the VM window/input qualifier against the immutable target desktop SDK."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))
from distro_build.desktop_native import _link_wrapper

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

sdk = json.loads((project / "out/sdk/current.json").read_text())
root = Path(sdk["sysroot"]).resolve()
sdk_report = json.loads(Path(sdk["report"]).read_text())
if sdk_report["identity"] != sdk["identity"] or Path(sdk_report["sysroot"]).resolve() != root:
    raise RuntimeError("Desktop SDK identity does not match its report")
bootstrap = project / "out/bootstrap/root"
tools = bootstrap / "tools"
compiler = tools / "pass2/bin/x86_64-custom-linux-gnu-gcc"
if not (tools / "pass2/.runtime-validated.json").is_file():
    raise RuntimeError("Source-built pass2 compiler has not passed its runtime audit")
source = Path(__file__).with_name("desktop-session-probe.c")
xmls = {
    "xdg-shell": root / "usr/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml",
    "presentation-time": root / "usr/share/wayland-protocols/stable/presentation-time/presentation-time.xml",
}
scanner = root / "usr/bin/wayland-scanner"
inputs = {"builder_sha256": digest(__file__), "source_sha256": digest(source),
          "sdk_identity": sdk["identity"], "sdk_report_sha256": digest(sdk["report"]),
          "compiler_sha256": digest(compiler), "scanner_sha256": digest(scanner),
          "protocols": {name: digest(path) for name, path in xmls.items()}}
identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
work = project / "out/qualification/desktop-session-probe" / identity
work.mkdir(parents=True, exist_ok=True)
env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
loader = root / "usr/lib/ld-linux-x86-64.so.2"
prefix = [str(loader), "--inhibit-cache", "--library-path", str(root / "usr/lib")]

def audit_loader(binary):
    result = subprocess.run([*prefix, "--list", str(binary)], env=env,
                            capture_output=True, text=True, check=True)
    (work / (binary.name + "-loader-list.txt")).write_text(result.stdout + result.stderr)
    libraries = []
    for line in result.stdout.splitlines():
        if "linux-vdso" in line:
            continue
        match = re.search(r"(?:=>\s*)?(/[^\s]+)\s+\(0x[0-9a-f]+\)", line)
        if not match or not Path(match[1]).resolve().is_relative_to(root):
            raise RuntimeError("Qualifier resolved a library outside its own SDK: " + line)
        file = Path(match[1]).resolve()
        libraries.append({"path": str(file.relative_to(root)), "sha256": digest(file)})
    return libraries

scanner_libraries = audit_loader(scanner)
generated = []
for name, xml in xmls.items():
    for mode, suffix in (("client-header", "-client.h"), ("private-code", "-protocol.c")):
        output = work / (name + suffix)
        subprocess.run([*prefix, str(scanner), mode, str(xml), str(output)], env=env, check=True)
        generated.append(output)
shutil.copy2(source, work / source.name)
shutil.copy2(__file__, work / Path(__file__).name)
wrapper = work / "target-cc"
_link_wrapper(wrapper, compiler, root, [root, tools, work])
objects = []
for file in [work / source.name, *(p for p in generated if p.suffix == ".c")]:
    obj = work / (file.stem + ".o")
    subprocess.run([str(wrapper), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I" + str(work),
                    "-c", str(file), "-o", str(obj)], env=env, check=True)
    objects.append(obj)
binary = work / "desktop-session-probe"
result = subprocess.run([str(wrapper), "-static-libgcc", "-Wl,--verbose", *map(str, objects),
                         "-lwayland-client", "-o", str(binary)], env=env,
                        capture_output=True, text=True, check=True)
(work / "link.log").write_text(result.stdout + result.stderr)
for opened in re.findall(r"attempt to open (.+) succeeded", result.stdout):
    file = Path(opened)
    if file.is_absolute() and not any(file.resolve().is_relative_to(p.resolve()) for p in (root, tools, work)):
        raise RuntimeError("Qualifier linked an undeclared host input: " + opened)
dynamic = subprocess.check_output(["readelf", "-d", str(binary)], env=env, text=True)
program = subprocess.check_output(["readelf", "-l", str(binary)], env=env, text=True)
if "(RPATH)" in dynamic or "(RUNPATH)" in dynamic:
    raise RuntimeError("Qualifier unexpectedly embeds a library search path")
if "Requesting program interpreter: /lib64/ld-linux-x86-64.so.2" not in program:
    raise RuntimeError("Qualifier has the wrong target ELF interpreter")
(work / "elf-dynamic.txt").write_text(dynamic)
libraries = audit_loader(binary)
help_result = subprocess.run([*prefix, str(binary), "--help"], env=env,
                             capture_output=True, text=True, check=True)
(work / "help.log").write_text(help_result.stdout + help_result.stderr)
negative = subprocess.run([*prefix, str(binary)], env={**env, "XDG_RUNTIME_DIR": str(work / "absent-runtime"),
                                                     "WAYLAND_DISPLAY": "absent-wayland"},
                          capture_output=True, text=True, timeout=5)
if negative.returncode != 3 or "CUSTOM_DESKTOP_WINDOW_INPUT_OK" in negative.stdout:
    raise RuntimeError("Missing Wayland session was incorrectly accepted")
(work / "missing-session.log").write_text(negative.stdout + negative.stderr)
report = {"identity": identity, "kind": "compiled-target-wayland-window-input-qualifier",
          "binary": str(binary), "sha256": digest(binary), "inputs": inputs,
          "sdk_packages": sdk_report["packages"], "loaded_libraries": libraries,
          "scanner_loaded_libraries": scanner_libraries,
          "generated": {file.name: digest(file) for file in generated},
          "compile_audit_passed": True, "missing_session_rejected": True,
          "runtime_qualified": False,
          "runtime_requirement": "Guest wl_surface frame and wp_presentation presented events, focused Q-key press, presented color-pattern redraw; success is reported only by the running client"}
(work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
current = {"identity": identity, "binary": str(binary), "sha256": report["sha256"],
           "report": str(work / "report.json"), "runtime_qualified": False}
(work.parent / "current.json").write_text(json.dumps(current, indent=2) + "\n")
print(json.dumps(current, indent=2))
