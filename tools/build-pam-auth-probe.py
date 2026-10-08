#!/usr/bin/env python3
"""Compile a genuine target PAM qualifier; never authenticate on the host.

--make-fixture prepares a disposable password/hash pair only. It does not change
an account, image, PAM policy, host service or OS package.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))
from distro_build.desktop_native import _link_wrapper
from distro_build.packaging import PacmanToolkit, inspect_package

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--make-fixture", action="store_true")
arguments = parser.parse_args()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


names = ("glibc", "libxcrypt", "pam")
records, package_info = {}, {}
for name in names:
    record = json.loads((project / "out/state/packages" / (name + ".json")).read_text())
    archive = Path(record["path"])
    if record["package"] != name or digest(archive) != record["sha256"]:
        raise RuntimeError(f"target PAM SDK archive identity differs: {name}")
    info = inspect_package(archive)
    if info["name"] != name or info["architecture"] != "x86_64":
        raise RuntimeError(f"target PAM SDK package metadata differs: {name}")
    records[name], package_info[name] = record, info
helper = next(entry for entry in package_info["pam"]["files"] if entry["path"] == "usr/sbin/unix_chkpwd")
if helper["type"] != "file" or helper["uid"] != 0 or not helper["mode"] & 0o4000:
    raise RuntimeError("PAM archive does not contain its genuine root-owned setuid shadow helper")

bootstrap = project / "out/bootstrap/root"
tools = bootstrap / "tools"
compiler = tools / "pass2/bin/x86_64-custom-linux-gnu-gcc"
environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
subprocess.run(["python3", "-B", str(project / "bootstrap/run-stage.py"),
    "--sources", str(project / "out/sources/downloads"), "--work", str(project / "out/bootstrap/work"),
    "--root", str(bootstrap), "--tools", str(tools), "--check", "--stage", "validate", "--stage", "gcc-pass2"],
    env=environment, check=True)
source = Path(__file__).with_name("pam-auth-probe.c")
headers = bootstrap / "usr/include"
header_files = {}
for file in sorted(headers.rglob("*")):
    if file.is_file():
        if not file.resolve().is_relative_to(bootstrap):
            raise RuntimeError("Validated bootstrap header points outside its declared root")
        header_files[str(file.relative_to(headers))] = digest(file)
header_identity = hashlib.sha256(json.dumps(header_files, sort_keys=True).encode()).hexdigest()
inputs = {"source_sha256": digest(source), "builder_sha256": digest(Path(__file__)),
          "compiler": str(compiler), "compiler_sha256": digest(compiler),
          "wrapper_sha256": digest(project / "src/distro_build/desktop_native.py"),
          "validated_bootstrap_headers": {"source": str(headers), "files": len(header_files), "sha256": header_identity},
          "sdk_packages": {name: {"version": package_info[name]["version"],
              "build_identity": records[name]["build_identity"], "sha256": records[name]["sha256"]} for name in names}}
identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
work = project / "out/qualification/pam-auth-probe" / identity
work.mkdir(parents=True, exist_ok=True)
(work / "tmp").mkdir(exist_ok=True)
environment["TMPDIR"] = str(work / "tmp")
root = work / "sysroot"
if root.exists():
    shutil.rmtree(root)
PacmanToolkit(project / "out/native-toolkit/prefix").install(root,
    [Path(records[name]["path"]) for name in names], bootstrap=True,
    expected_hashes={Path(record["path"]).name: record["sha256"] for record in records.values()})
shutil.copytree(headers, root / "usr/include", symlinks=True, dirs_exist_ok=True)
(work / "bootstrap-headers.json").write_text(json.dumps(header_files, indent=2) + "\n")
for file in (source, Path(__file__)):
    shutil.copy2(file, work / file.name)
wrapper = work / "target-cc"
_link_wrapper(wrapper, compiler, root, [root, tools, work])
binary = work / "pam-auth-probe"
compiled = subprocess.run([str(wrapper), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-static-libgcc",
    "-Wl,--verbose", str(work / source.name), "-lpam", "-lcrypt", "-o", str(binary)],
    env=environment, capture_output=True, text=True)
(work / "link.log").write_text(compiled.stdout + compiled.stderr)
if compiled.returncode:
    sys.stderr.write(compiled.stderr)
    compiled.check_returncode()
for opened in re.findall(r"attempt to open (.+) succeeded", compiled.stdout):
    file = Path(opened)
    if file.is_absolute() and not any(file.resolve().is_relative_to(path.resolve()) for path in (root, tools, work)):
        raise RuntimeError("PAM qualifier linked an undeclared host input: " + opened)
readelf = tools / "bin/x86_64-custom-linux-gnu-readelf"
dynamic = subprocess.check_output([str(readelf), "-d", str(binary)], env=environment, text=True)
program = subprocess.check_output([str(readelf), "-l", str(binary)], env=environment, text=True)
if "(RPATH)" in dynamic or "(RUNPATH)" in dynamic:
    raise RuntimeError("PAM qualifier contains an embedded search path")
if "Requesting program interpreter: /lib64/ld-linux-x86-64.so.2" not in program:
    raise RuntimeError("PAM qualifier has an unexpected interpreter")
(work / "elf-dynamic.txt").write_text(dynamic)
loader = root / "usr/lib/ld-linux-x86-64.so.2"
prefix = [str(loader), "--inhibit-cache", "--library-path", str(root / "usr/lib")]
listed = subprocess.run([*prefix, "--list", str(binary)], env=environment,
    capture_output=True, text=True, check=True)
(work / "loader-list.log").write_text(listed.stdout + listed.stderr)
loaded = []
for line in listed.stdout.splitlines():
    if "linux-vdso" in line:
        continue
    match = re.search(r"(?:=>\s*)?(/[^\s]+)\s+\(0x[0-9a-f]+\)", line)
    if not match or not Path(match[1]).resolve().is_relative_to(root):
        raise RuntimeError("PAM qualifier loaded an undeclared host library: " + line)
    library = Path(match[1]).resolve()
    loaded.append({"path": str(library.relative_to(root)), "sha256": digest(library)})
self_test = subprocess.run([*prefix, str(binary), "--self-test"], env=environment,
    capture_output=True, text=True, check=True)
if self_test.stdout.strip() != "CUSTOM_PAM_CONVERSATION_SELF_TEST_OK":
    raise RuntimeError("PAM password conversation self-test failed")
(work / "conversation.log").write_text(self_test.stdout + self_test.stderr)
refused = subprocess.run([*prefix, str(binary), "--expect-deny", "custom"], env=environment,
    input="unused-disposable-input\n", capture_output=True, text=True, timeout=5)
if refused.returncode != 2 or "CUSTOM_PAM_AUTH_PRECONDITIONS_FAILED" not in refused.stderr:
    raise RuntimeError("Guest-only PAM qualifier did not refuse the host context")
(work / "host-context-refused.log").write_text(refused.stdout + refused.stderr)
fixture = None
if arguments.make_fixture:
    directory = work / "disposable-fixture"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    if (directory / "password").exists():
        raise RuntimeError("Disposable fixture already exists; reuse it explicitly or remove only that private directory")
    password = secrets.token_urlsafe(32) + "\n"
    hashed = subprocess.run([*prefix, str(binary), "--fixture-hash"], env=environment,
        input=password, capture_output=True, text=True, check=True).stdout
    if not re.fullmatch(r"\$6\$rounds=100000\$[^\s:]+\n", hashed):
        raise RuntimeError("Target libxcrypt did not produce the requested disposable SHA-512 hash")
    for name, value in (("password", password), ("password.hash", hashed),
                        ("wrong-password", secrets.token_urlsafe(32) + "\n")):
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(value)
    fixture = {"directory": str(directory), "user": "custom", "uid": 1000,
               "purpose": "disposable private VM only; not enrolled or included in any image by this tool"}
report = {"identity": identity, "kind": "compiled-target-guest-password-authentication-qualifier",
    "binary": str(binary), "sha256": digest(binary), "inputs": inputs,
    "shadow_helper": helper, "loaded_libraries": loaded, "conversation_self_test_passed": True,
    "host_authentication_refused": True, "runtime_qualified": False, "disposable_fixture": fixture,
    "guest_requirement": "UID 1000, private VM marker, root-owned setuid unix_chkpwd, shadow unreadable, real login pam_unix policy; locked/wrong password PAM_AUTH_ERR and temporary correct password PAM_SUCCESS plus account success"}
(work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
current = {"identity": identity, "binary": str(binary), "sha256": report["sha256"],
           "report": str(work / "report.json"), "runtime_qualified": False, "disposable_fixture": fixture}
(work.parent / "current.json").write_text(json.dumps(current, indent=2) + "\n")
print(json.dumps(current, indent=2))
