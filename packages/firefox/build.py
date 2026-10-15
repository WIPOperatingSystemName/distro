#!/usr/bin/env python3
"""Build Firefox against the distro sysroot; native tools stay private."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.desktop_services import ServiceBuild
from distro_build.desktop_native import _link_wrapper
from build_temp import temporary_environment

b = ServiceBuild()
recipe = Path(__file__).parent
manifest = tomllib.loads((recipe / "package.toml").read_text())
seed = json.loads(Path(os.environ["CD_RUST_SEED"]).read_text())
rust = Path(seed["rust_sysroot"])
b.env.update({"RUSTC": str(rust / "bin/rustc"), "CARGO": str(rust / "bin/cargo"),
              "RUSTUP_TOOLCHAIN": manifest["build"]["rust_toolchain"],
              "LIBCLANG_PATH": str(Path(seed["libclang"]["path"]).parent)})

# Cargo resolves the host-only cbindgen through verified, source-pinned crates.
# Its own upstream lockfile supplies every registry checksum in the manifest.
vendor = b.work / "cbindgen-vendor"
vendor.mkdir()
for source in manifest["sources"]:
    if not source["name"].startswith("crate-"):
        continue
    tree = b.secondary(source["name"])
    destination = vendor / tree.name
    shutil.copytree(tree, destination)
    checksums = {str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(destination.rglob("*")) if path.is_file()}
    (destination / ".cargo-checksum.json").write_text(json.dumps(
        {"files": checksums, "package": source["sha256"]}))
cbindgen = b.secondary("cbindgen")
(cbindgen / ".cargo").mkdir(exist_ok=True)
(cbindgen / ".cargo/config.toml").write_text(
    '[source.crates-io]\nreplace-with = "vendored"\n'
    f'[source.vendored]\ndirectory = {json.dumps(str(vendor))}\n')
native_env = b.host_env()
native_env.update({"CARGO_HOME": str(b.work / "native-cargo-home"),
                   "CARGO_TARGET_DIR": str(b.work / "native-cargo-target")})
b.host_run([str(rust / "bin/cargo"), "build", "--release", "--locked", "--offline",
            "--jobs", b.jobs], cbindgen, native_env)
(b.native / "bin").mkdir(parents=True, exist_ok=True)
shutil.copy2(b.work / "native-cargo-target/release/cbindgen", b.native / "bin/cbindgen")
shutil.copy2(recipe / "unzip.py", b.native / "bin/unzip")
(b.native / "bin/unzip").chmod(0o755)

nasm_build = b.work / "native-nasm"
nasm_build.mkdir()
b.host_run([str(b.secondary("nasm") / "configure"), f"--prefix={b.native}"], nasm_build)
b.host_run(["make", f"-j{b.jobs}"], nasm_build)
b.host_run(["make", "install"], nasm_build)

# Node is an explicitly pinned upstream build seed, never a guest package.
node = b.secondary("node-seed") / "bin/node"
llvm = b.secondary("llvm-tools-seed") / "llvm-tools-preview/lib/rustlib/x86_64-unknown-linux-gnu/bin"
wasi = b.secondary("wasi-seed")
(b.work / "native-seeds.json").write_text(json.dumps({
    "node": {"kind": "pinned-upstream-host-build-seed", "path": str(node),
             "sha256": hashlib.sha256(node.read_bytes()).hexdigest()},
    "llvm-tools": {"kind": "pinned-upstream-host-build-seed", "version": "nightly-2026-10-06"},
    "wasi-sdk": {"kind": "pinned-upstream-wasm-build-seed", "version": "29.0"},
    "cbindgen": {"kind": "source-built-private-host-tool", "version": "0.29.4"},
    "nasm": {"kind": "source-built-private-host-tool", "version": "2.16.03"},
}, indent=2) + "\n")

cxx = b.work / "target-cxx"
gcc_install = next((b.tools / "pass2/lib/gcc" / b.target).iterdir())
bindgen_flags = f"--sysroot={b.sysroot} --gcc-install-dir={gcc_install}"
obj = b.work / "obj"
config = b.work / "mozconfig"
options = ["--enable-application=browser", "--enable-release", "--enable-official-branding",
           "--enable-default-toolkit=cairo-gtk3-wayland-only", "--disable-bootstrap",
           "--disable-debug", "--disable-debug-symbols", "--disable-tests",
           "--disable-updater", "--disable-crashreporter", "--disable-elf-hack",
           "--enable-linker=bfd", "--enable-audio-backends=alsa,pulseaudio",
           "--with-distribution-id=custom-distro", "--prefix=/usr", "--libdir=/usr/lib",
           "--target=x86_64-unknown-linux-gnu", "--host=x86_64-unknown-linux-gnu",
           f"--with-wasi-sysroot={wasi / 'share/wasi-sysroot'}",
           f"--with-clang-path={seed['clang']['path']}",
           f"--with-libclang-path={Path(seed['libclang']['path']).parent}"]
config.write_text(f"mk_add_options MOZ_OBJDIR={obj}\n" +
                  "\n".join("ac_add_options " + option for option in options) + "\n")
b.env.update({"MOZCONFIG": str(config), "MOZBUILD_STATE_PATH": str(b.work / "mozbuild-state"),
              "MACH_BUILD_PYTHON_NATIVE_PACKAGE_SOURCE": "none", "MOZ_NOSPAM": "1",
              "CXX": str(cxx), "CXXFLAGS": f"-O2 -g0 -fPIC -ffile-prefix-map={b.work}=/usr/src/firefox-build",
              "CFLAGS": b.env["CFLAGS"] + f" -ffile-prefix-map={b.work}=/usr/src/firefox-build", "HOST_CC": "/usr/bin/gcc",
              "HOST_CXX": "/usr/bin/g++", "HOST_CFLAGS": "-O2 -g0", "HOST_CXXFLAGS": "-O2 -g0",
              "HOST_LDFLAGS": "", "NODEJS": str(node), "CBINDGEN": str(b.native / "bin/cbindgen"),
              "NASM": str(b.native / "bin/nasm"), "LLVM_OBJDUMP": str(llvm / "llvm-objdump"),
              "WASM_CC": str(wasi / "bin/clang"), "WASM_CXX": str(wasi / "bin/clang++"), "PYTHON3": "/usr/bin/python3",
              "CARGO_HOME": str(b.work / "cargo-home"), "BINDGEN_CFLAGS": bindgen_flags,
              "PATH": str(b.native / "bin") + ":" + str(llvm) + ":" + b.env["PATH"],
              "RUSTFLAGS": f"-C linker={cxx} -C link-arg=-static-libgcc --remap-path-prefix={b.work}=/usr/src/firefox-build"})
# The hashed work directory is too deep for Python's multiprocessing Manager
# socket. Keep Mach's private temporary files under a short path in out/.
output = Path(os.environ["CD_BOOTSTRAP_ROOT"]).parents[1]
with temporary_environment(b.env, output) as environment:
    b.env = environment
    temporary = b.env["TMPDIR"]
    # Configure compiles temporary objects here. Admit only this private build's
    # directory alongside the existing work/SDK roots.
    allowed = [b.sysroot, b.tools, b.work, rust, Path(temporary)]
    _link_wrapper(b.wrapper, b.cc, b.sysroot, allowed)
    _link_wrapper(cxx, b.tools / "pass2/bin" / f"{b.target}-g++", b.sysroot, allowed)
    b.env["CFLAGS"] += f" -ffile-prefix-map={temporary}=/usr/src/firefox-tmp"
    b.env["CXXFLAGS"] += f" -ffile-prefix-map={temporary}=/usr/src/firefox-tmp"
    b.env["RUSTFLAGS"] += f" --remap-path-prefix={temporary}=/usr/src/firefox-tmp"
    b.run(["python3", "mach", "configure"])
    # Configure records host flags. Exporting them during make also exports its
    # per-object dependency-file flags to Cargo, whose build scripts run elsewhere.
    for variable in ("HOST_CFLAGS", "HOST_CXXFLAGS", "HOST_LDFLAGS"):
        b.env.pop(variable, None)
    b.run(["python3", "mach", "build", f"-j{b.jobs}"])
    b.run(["make", "-C", str(obj), "install", f"DESTDIR={b.stage}"])
b.license("firefox", ["LICENSE"])

entry = b.stage / "usr/share/applications/firefox.desktop"
entry.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(recipe / "firefox.desktop", entry)
icon = b.stage / "usr/share/icons/hicolor/128x128/apps/firefox.png"
icon.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(b.source / "browser/branding/official/default128.png", icon)

# Firefox loads source-built private libraries beside the browser. Only these
# and declared sysroot libraries may satisfy ELF dependencies; host paths fail.
provided = {p.name for p in (b.stage / "usr/lib/firefox").iterdir()}
provided.update(p.name for p in (b.sysroot / "usr/lib").iterdir())
audit = []
for path in sorted(b.stage.rglob("*")):
    if path.is_symlink() or not path.is_file():
        continue
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            continue
    dynamic = subprocess.check_output([str(b.readelf), "-d", str(path)], text=True)
    program = subprocess.check_output([str(b.readelf), "-l", str(path)], text=True)
    needed = re.findall(r"\(NEEDED\).*\[([^]]+)\]", dynamic)
    missing = set(needed) - provided
    if missing:
        raise RuntimeError(f"Firefox has undeclared runtime libraries: {path}: {sorted(missing)}")
    for runpath in re.findall(r"\((?:RPATH|RUNPATH)\).*\[([^]]+)\]", dynamic):
        if runpath != "$ORIGIN":
            raise RuntimeError(f"Firefox has an unexpected library search path: {path}: {runpath}")
    interpreters = re.findall(r"Requesting program interpreter: ([^]]+)", program)
    if any(value != "/lib64/ld-linux-x86-64.so.2" for value in interpreters):
        raise RuntimeError(f"Firefox has an unexpected interpreter: {path}")
    audit.append({"file": str(path.relative_to(b.stage)), "needed": needed,
                  "interpreter": interpreters})
if not audit:
    raise RuntimeError("Firefox package contains no compiled browser")
(b.work / "target-elf-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
from audit import audit_payload
audit_payload(b)
