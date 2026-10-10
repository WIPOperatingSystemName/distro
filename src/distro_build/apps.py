"""Recorded Telorgon sources and target-only desktop application builds.

The application catalog stays separate from the upstream archive catalog until
its native target packages are available. Missing inputs are reported explicitly;
host SDK libraries are never substituted for those inputs.
"""

from __future__ import annotations

import ctypes.util
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib

from .boot import EFI_TOOLCHAIN, _git_metadata, sha256


RUST_TARGET = "x86_64-unknown-linux-gnu"
GNU_TARGET = "x86_64-custom-linux-gnu"
INPUTS = {
    "telorgon": ("Cargo.toml", "Cargo.lock", "LICENSE", "README.md", "crates", "third_party", "packaging"),
    "test-shell": ("Cargo.toml", "Cargo.lock", "src", "assets", "licenses", "tests"),
    "telorgon-file-explorer": ("Cargo.toml", "Cargo.lock", "src", "assets", "licenses", "packaging"),
    "telorgon-settings-app": ("Cargo.toml", "Cargo.lock", "src", "assets"),
    "telorgon-portal-picker": ("Cargo.toml", "Cargo.lock", "src", "assets", "licenses"),
}
EXCLUDED = {".git", "target", "vendor", "out", "__pycache__", ".cache"}
APP_DIRECTORIES = ("telorgon-shell", "file-explorer", "settings-app", "portal-picker")


def catalog(project: Path) -> dict[str, dict]:
    """Read actual app manifests without manufacturing remote source identities."""
    project = Path(project).resolve()
    result = {}
    for directory in APP_DIRECTORIES:
        path = project / "packages" / directory / "application.toml"
        document = tomllib.loads(path.read_text())
        if document.get("schema") != 1:
            raise RuntimeError(f"Unsupported application catalog schema: {path}")
        package = document["package"]
        name = package["name"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9+_.-]*", name) or name in result:
            raise RuntimeError(f"Invalid or duplicate application package name: {path}")
        source = document["source"]
        if (source.get("kind") != "local-content-snapshot" or source["primary"] not in INPUTS
                or not set(source["projects"]).issubset(INPUTS)
                or source["primary"] not in source["projects"]):
            raise RuntimeError(f"Application source inputs must name known real projects: {path}")
        if document["build"].get("adapter") != "target-cargo":
            raise RuntimeError(f"Unsupported application adapter: {path}")
        result[name] = {**document, "recipe": str(path)}
    return result


def plan(project: Path, names: list[str] | None = None) -> dict:
    applications = catalog(project)
    selected = names or list(applications)
    if any(name not in applications for name in selected):
        raise RuntimeError("Unknown Telorgon application package")
    return {"kind": "target-desktop-application-plan", "applications": [
        {"name": name, "source_projects": applications[name]["source"]["projects"],
         "binary": applications[name]["build"]["binary"],
         "requirements": applications[name]["requirements"],
         "runtime_packages": applications[name]["runtime"]["packages"]}
        for name in selected],
        "graphics": {"shell_policy": "Auto (recorded distro source overlay)",
                     "software_path": "existing CPU renderer and DRM/KMS dumb buffers",
                     "vulkan_path": "Vulkan adapter must match the selected DRM device",
                     "screen_cast": "enabled when the recorded Telorgon sources declare software-screencast support",
                     "qualification": "QEMU CPU KMS path and physical GPU paths require separate tests"},
        "native_build_rule": "all target libraries and headers come from the recorded distro sysroot",
        "rust_seed_rule": "installed pinned Rust compiler/standard library is a declared bootstrap seed"}


def _copy_inputs(source: Path, destination: Path, inputs: tuple[str, ...]) -> dict:
    source = source.resolve(strict=True)
    destination.mkdir(parents=True)
    records = []

    def copy(path: Path) -> None:
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(source) or not resolved.is_file():
                raise RuntimeError(f"Source symlink escapes its recorded tree: {path}")
        elif path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                if child.name not in EXCLUDED:
                    copy(child)
            return
        elif not path.is_file():
            raise RuntimeError(f"Unsupported application source file type: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target, follow_symlinks=True)
        records.append({"path": relative.as_posix(), "sha256": sha256(target),
                        "mode": stat.S_IMODE(target.stat().st_mode), "size": target.stat().st_size})

    for name in inputs:
        path = source / name
        if path.exists() or path.is_symlink():
            copy(path)
    for required in ("Cargo.toml", "Cargo.lock"):
        if not (destination / required).is_file():
            raise RuntimeError(f"Source snapshot requires {source / required}")
    identity = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"source": str(source), "identity": identity, "files": records,
            "cargo_lock_sha256": sha256(destination / "Cargo.lock"), **_git_metadata(source)}


def _software_capture_supported(workspace: Path) -> bool:
    manifest = workspace / "telorgon/crates/telorgon/Cargo.toml"
    if not manifest.is_file():
        return False
    package = tomllib.loads(manifest.read_text()).get("package", {})
    return package.get("metadata", {}).get("telorgon", {}).get("software-screencast") is True


def _shell_overlays(workspace: Path) -> list[dict]:
    path = workspace / "test-shell/src/main.rs"
    before = path.read_text()
    launcher = ('    if let Err(error) = desktop_entry::ensure_settings_entry() {\n'
                '        eprintln!("Could not create the Settings launcher entry: {error}");\n'
                '    }\n')
    if (before.count(launcher) != 1 or before.count(".renderer(Renderer::Vulkan)") != 1
            or before.count(".capture(Capture::desktop())") != 1):
        raise RuntimeError("Shell source changed; review distro overlay anchors before building")
    after = (before.replace(launcher, "", 1)
             .replace(".renderer(Renderer::Vulkan)", ".renderer(Renderer::Auto)", 1))
    capture = _software_capture_supported(workspace)
    if not capture:
        after = after.replace(".capture(Capture::desktop())", ".capture(Capture::new())", 1)
    path.write_text(after)
    return [{"path": "test-shell/src/main.rs", "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
             "after_sha256": sha256(path),
             "changes": ["use existing Auto renderer to permit CPU DRM/KMS fallback",
                         ("retain consent-based ScreenCast capture for Vulkan and software renderers" if capture
                          else "disable capture for older sources without software capture support"),
                         "use packaged Settings launcher instead of developer target/debug path"]}]


def prepare(project: Path, output: Path | None = None, *, source_root: Path | None = None) -> dict:
    """Snapshot dirty or clean source contents and preserve each Cargo lockfile."""
    project = Path(project).resolve()
    output = Path(output or project / "out/apps").resolve()
    source_root = Path(source_root or project / "sources").resolve()
    origin = {"kind": "local-workspace", "root": str(source_root)}
    portable = None
    if (source_root / "source-manifest.json").exists():
        # Import locally: source_bundle imports the source allowlist above.
        from .source_bundle import verify_workspace
        portable = verify_workspace(source_root)
        origin = {"kind": "verified-portable-source-bundle", "root": str(source_root),
                  "identity": portable["identity"],
                  "manifest_sha256": sha256(source_root / "source-manifest.json")}
    output.mkdir(parents=True, exist_ok=True)
    snapshots = output / "sources"
    snapshots.mkdir(exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="prepare-", dir=snapshots))
    try:
        sources = {name: _copy_inputs(source_root / name, temporary / name, inputs)
                   for name, inputs in INPUTS.items()}
        if portable is not None:
            verify_workspace(source_root)
            for name, record in sources.items():
                record["origin"] = {"kind": origin["kind"], "identity": portable["sources"][name]["identity"]}
                for key in ("git_head", "git_dirty"):
                    if key in portable["sources"][name]:
                        record[key] = portable["sources"][name][key]
        overlays = _shell_overlays(temporary)
        identity = hashlib.sha256(json.dumps({"sources": {k: v["identity"] for k, v in sources.items()},
                                             "overlays": overlays}, sort_keys=True).encode()).hexdigest()
        workspace = snapshots / identity
        if workspace.exists():
            shutil.rmtree(temporary)
        else:
            temporary.rename(workspace)
        result = {"schema": 1, "kind": "local-content-snapshot", "identity": identity,
                  "workspace": str(workspace), "sources": sources, "overlays": overlays, "origin": origin,
                  "cargo_locks": {name: record["cargo_lock_sha256"] for name, record in sources.items()},
                  "manifest": str(output / "sources.json")}
        Path(result["manifest"]).write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _library(root: Path, filename: str) -> Path | None:
    for relative in ("usr/lib", "lib", "usr/lib64", "lib64", "usr/lib/x86_64-linux-gnu"):
        candidate = root / relative / filename
        if candidate.is_file():
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise RuntimeError(f"Target library symlink escapes its sysroot: {candidate}")
            return resolved
    return None


def _pc_environment(sysroot: Path) -> dict:
    return {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
            "PKG_CONFIG_SYSROOT_DIR": str(sysroot), "PKG_CONFIG_PATH": "",
            "PKG_CONFIG_LIBDIR": os.pathsep.join(str(sysroot / directory) for directory in
                                                ("usr/lib/pkgconfig", "usr/share/pkgconfig", "usr/lib64/pkgconfig")),
            "PKG_CONFIG_ALLOW_CROSS": "1"}


def preflight(project: Path, sysroot: Path | None = None, *, tools: Path | None = None,
              names: list[str] | None = None) -> dict:
    project = Path(project).resolve()
    sysroot = Path(sysroot or project / "out/bootstrap/root").resolve()
    tools = Path(tools or project / "out/bootstrap/root/tools").resolve()
    applications = catalog(project)
    selected = names or list(applications)
    if any(name not in applications for name in selected):
        raise RuntimeError("Unknown Telorgon application package")
    compiler = tools / "pass2/bin" / f"{GNU_TARGET}-gcc"
    if not (tools / "pass2/.runtime-validated.json").is_file() or not compiler.is_file():
        compiler = tools / "bin" / f"{GNU_TARGET}-gcc"
    target_inputs = {"compiler": str(compiler), "compiler_exists": compiler.is_file(),
                     "sysroot": str(sysroot), "source_built_libc_report": str(sysroot / ".bootstrap-validated.json"),
                     "libc_report_exists": (sysroot / ".bootstrap-validated.json").is_file()}
    host_tools = {name: shutil.which(name) for name in ("cargo", "rustup", "pkg-config", "readelf", "bash")}
    libclang = ctypes.util.find_library("clang")
    if not libclang:
        candidates = sorted(Path("/usr/lib").glob("llvm-*/lib/libclang.so*"))
        libclang = str(candidates[-1]) if candidates else None
    reports = []
    for name in selected:
        app = applications[name]
        requirements = app["requirements"]
        libraries = {}
        missing = []
        for library in requirements["link_libraries"]:
            path = _library(sysroot, f"lib{library}.so")
            libraries[library] = str(path) if path else None
            if path is None:
                missing.append(f"target link library lib{library}.so")
        for module in requirements.get("pkg_config", []):
            command = host_tools["pkg-config"]
            result = subprocess.run([command, "--exists", module], env=_pc_environment(sysroot),
                                    capture_output=True, check=False) if command else None
            if result is None or result.returncode:
                missing.append(f"target pkg-config module {module}")
        if requirements.get("wayland_xml"):
            for relative in ("usr/share/wayland/wayland.xml", "usr/share/wayland-protocols"):
                if not (sysroot / relative).exists():
                    missing.append(f"target protocol data {relative}")
        if requirements.get("bindgen") and not libclang:
            missing.append("declared host libclang for bindgen")
        if not compiler.is_file():
            missing.append("source-built target cross compiler")
        if not target_inputs["libc_report_exists"]:
            missing.append("source-built target libc validation report")
        for tool in ("cargo", "rustup", "pkg-config", "readelf"):
            if not host_tools[tool]:
                missing.append(f"declared host {tool} seed tool")
        runtime_missing = [path for path in app["runtime"].get("files", [])
                           if not (sysroot / path.lstrip("/")).exists()]
        reports.append({"name": name, "ready_to_build": not missing, "missing_build_inputs": missing,
                        "libraries": libraries, "runtime_packages": app["runtime"]["packages"],
                        "missing_runtime_files": runtime_missing,
                        "runtime_qualification": "not established by this preflight"})
    return {"kind": "target-desktop-preflight", "target_inputs": target_inputs, "host_tools": host_tools,
            "host_libclang": libclang, "applications": reports,
            "ready_to_build": all(record["ready_to_build"] for record in reports)}


def _verify_snapshot(manifest: dict) -> Path:
    workspace = Path(manifest["workspace"]).resolve(strict=True)
    overlay_map = {record["path"]: record["after_sha256"] for record in manifest["overlays"]}
    for name, source in manifest["sources"].items():
        for record in source["files"]:
            path = workspace / name / record["path"]
            expected = overlay_map.get(f"{name}/{record['path']}", record["sha256"])
            if (path.is_symlink() or not path.is_file() or sha256(path) != expected
                    or stat.S_IMODE(path.stat().st_mode) != record["mode"]):
                raise RuntimeError(f"Recorded application source changed: {path}")
        if sha256(workspace / name / "Cargo.lock") != manifest["cargo_locks"][name]:
            raise RuntimeError(f"Recorded Cargo lockfile changed: {name}")
    return workspace


def _link_wrapper(path: Path, compiler: Path, sysroot: Path, allowed: list[Path]) -> None:
    script = ("#!/usr/bin/python3\nimport os,pathlib,re,sys\n"
              f"compiler={str(compiler)!r}\nsysroot={str(sysroot)!r}\n"
              f"allowed={[str(p.resolve()) for p in allowed]!r}\n"
              "def check(value):\n"
              "    if value.startswith('/') and not any(pathlib.Path(value).resolve().is_relative_to(root) for root in allowed):\n"
              "        sys.exit('Target compiler refused a host include/library path: '+value)\n"
              "arguments=sys.argv[1:]\n"
              "for index,argument in enumerate(arguments):\n"
              "    if argument in ('-L','-I','-isystem','-iquote') and index+1<len(arguments):\n"
              "        check(arguments[index+1])\n"
              "    elif argument.startswith(('-L','-I')) and len(argument)>2:\n"
              "        check(argument[2:])\n"
              "    elif re.search(r'\\.(?:so(?:\\.\\d+)*|a|o|rlib)$',argument):\n"
              "        check(argument)\n"
              "    elif argument.startswith('-Wl,'):\n"
              "        tokens=argument[4:].split(',')\n"
              "        for token in tokens:\n"
              "            if token.startswith('-L'):\n"
              "                check(token[2:])\n"
              "            elif token.startswith('/'):\n"
              "                check(token)\n"
              "options=['--sysroot='+sysroot,'-march=x86-64','-mtune=generic']\n"
              "if not any(argument in arguments for argument in ('-c','-E','-S','-M','-MM')):\n"
              "    options+=['-Wl,--dynamic-linker=/lib64/ld-linux-x86-64.so.2']\n"
              "os.execv(compiler,[compiler,*options,*arguments])\n")
    path.write_text(script)
    path.chmod(0o755)


def _sysroot_identity(sysroot: Path) -> str:
    digest = hashlib.sha256()
    for relative in ("usr/include", "usr/lib", "usr/lib64", "usr/share/wayland", "usr/share/wayland-protocols"):
        base = sysroot / relative
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            digest.update(path.relative_to(sysroot).as_posix().encode() + b"\0")
            if path.is_symlink():
                digest.update(os.fsencode(os.readlink(path)))
            elif path.is_file():
                digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def _readelf(path: Path, *options: str) -> str:
    result = subprocess.run(["readelf", *options, str(path)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"ELF inspection failed for {path}: {result.stderr.strip()}")
    return result.stdout


def _elf_closure(binary: Path, sysroot: Path) -> dict:
    program = _readelf(binary, "-l")
    interpreter = re.search(r"Requesting program interpreter: ([^\]]+)", program)
    expected = "/lib64/ld-linux-x86-64.so.2"
    if not interpreter or interpreter.group(1) != expected:
        raise RuntimeError("Application has the wrong target ELF interpreter")
    if not (sysroot / expected.lstrip("/")).is_file():
        raise RuntimeError("Target ELF interpreter is missing from the distro root")
    pending = [binary]
    seen = set()
    closure = []
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        dynamic = _readelf(path, "-d")
        if re.search(r"\((RPATH|RUNPATH)\)", dynamic):
            raise RuntimeError(f"Runtime search paths must not embed build/host directories: {path}")
        required = re.findall(r"\(NEEDED\).*\[([^\]]+)\]", dynamic)
        closure.append({"path": str(path), "sha256": sha256(path), "needed": required})
        for name in required:
            resolved = _library(sysroot, name)
            if resolved is None:
                raise RuntimeError(f"Application closure lacks target {name}, required by {path.name}")
            pending.append(resolved)
    return {"interpreter": expected, "closure": closure,
            "scope": "ELF linkage only; runtime dlopen/service/portal qualification remains required"}


def _write(stage: Path, relative: str, content: str, mode: int = 0o644) -> None:
    path = stage / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(mode)


def _install(app: dict, workspace: Path, binary: Path, stage: Path,
             cargo_home: Path | None = None, rust_sysroot: Path | None = None) -> dict:
    name = app["package"]["name"]
    destination = stage / "usr/bin" / app["build"]["binary"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, destination)
    destination.chmod(0o755)
    licenses = stage / "usr/share/licenses" / name
    licenses.mkdir(parents=True)
    shutil.copy2(workspace / "telorgon/LICENSE", licenses / "GPL-3.0-or-later.txt")
    source = workspace / app["source"]["primary"]
    if (source / "licenses").is_dir():
        shutil.copytree(source / "licenses", licenses / "assets", dirs_exist_ok=True)
    notices = []

    def preserve(root: Path, destination: Path) -> None:
        for path in sorted(root.rglob("*")):
            if (not path.is_file() or path.is_symlink() or not re.match(
                    r"^(?:LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT|OFL)(?:$|[._-])", path.name, re.I)):
                continue
            target = destination / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            notices.append({"path": str(target.relative_to(licenses)), "sha256": sha256(target)})

    preserve(workspace / "telorgon/crates/telorgon/resources/fonts", licenses / "fonts")
    preserve(workspace / "telorgon/third_party", licenses / "source-patches")
    if cargo_home is not None:
        locked = set()
        for project_name in app["source"]["projects"]:
            lock = tomllib.loads((workspace / project_name / "Cargo.lock").read_text())
            locked.update((p["name"], p["version"]) for p in lock["package"] if p.get("source", "").startswith("registry+"))
        for registry in sorted((cargo_home / "registry/src").glob("index.crates.io-*")):
            for crate_name, version in sorted(locked):
                crate = registry / f"{crate_name}-{version}"
                if crate.is_dir():
                    preserve(crate, licenses / "locked-crates" / crate.name)
    if rust_sysroot is not None:
        documentation = rust_sysroot / "share/doc/rust"
        library_copyright = documentation / "COPYRIGHT-library.html"
        if library_copyright.is_file():
            target = licenses / "rust-standard-library/COPYRIGHT-library.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(library_copyright, target)
            notices.append({"path": str(target.relative_to(licenses)), "sha256": sha256(target)})
        preserve(documentation / "licenses", licenses / "rust-standard-library/licenses")
    if name == "telorgon-file-explorer":
        command = ["bash", str(source / "packaging/install.sh"), "--prefix", "/usr", "--destdir", str(stage),
                   "--binary", str(binary)]
        subprocess.run(command, check=True)
    elif name == "telorgon-settings-app":
        _write(stage, "usr/share/applications/org.telorgon.settings.desktop",
               "[Desktop Entry]\nType=Application\nName=Telorgon Settings\n"
               "Exec=/usr/bin/telorgon-settings-app\nIcon=preferences-system\nTerminal=false\nCategories=Settings;\n")
    elif name == "telorgon-shell":
        _write(stage, "usr/bin/telorgon-session",
               "#!/bin/sh\nexport XDG_CURRENT_DESKTOP=telorgon-test-shell\n"
               "export XDG_SESSION_DESKTOP=telorgon-test-shell\nexport XDG_SESSION_TYPE=wayland\n"
               "exec /usr/bin/telorgon-test-shell\n", 0o755)
        _write(stage, "usr/share/wayland-sessions/telorgon.desktop",
               "[Desktop Entry]\nName=Telorgon\nComment=Custom Distro Telorgon session\n"
               "Exec=/usr/bin/telorgon-session\nType=Application\nDesktopNames=telorgon-test-shell;\n")
        capture = _software_capture_supported(workspace)
        _write(stage, "usr/share/xdg-desktop-portal/telorgon-test-shell-portals.conf",
               "[preferred]\norg.freedesktop.impl.portal.FileChooser=telorgon-file-explorer\n"
               f"org.freedesktop.impl.portal.ScreenCast={'telorgon' if capture else 'none'}\n")
        if capture:
            _write(stage, "usr/share/xdg-desktop-portal/portals/telorgon.portal",
                   "[portal]\nDBusName=org.freedesktop.impl.portal.desktop.telorgon\n"
                   "Interfaces=org.freedesktop.impl.portal.ScreenCast;\nUseIn=telorgon-test-shell;\n")
        _write(stage, "usr/share/custom-distro/capabilities/telorgon-shell.json", json.dumps({
            "profile": "desktop", "renderer": "Auto", "screen_cast": capture,
            "capture_backends": ["Vulkan", "Software"] if capture else [],
            "file_chooser_portal": "telorgon-file-explorer", "portal_picker_packaged": True}, indent=2) + "\n")
    inventory = {"scope": "embedded fonts, recorded source patches, cached locked Rust crate notices and declared Rust seed library notices",
                 "files": notices}
    _write(stage, f"usr/share/licenses/{name}/notices.json", json.dumps(inventory, indent=2) + "\n")
    return inventory


def build_app(project: Path, name: str, sysroot: Path | None = None, tools: Path | None = None,
              output: Path | None = None, *, offline: bool = True, jobs: int = 4,
              toolchain: str = EFI_TOOLCHAIN, manifest: Path | None = None,
              profile: str = "release", development: bool = False) -> dict:
    if name not in catalog(project):
        raise RuntimeError(f"Unknown application package: {name}")
    if profile not in {"dev", "release"}:
        raise RuntimeError("Cargo build profile must be dev or release")
    locks = project / "out/state/locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / f"app-{name}.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _build_app(project, name, sysroot, tools, output, offline=offline, jobs=jobs,
                          toolchain=toolchain, manifest=manifest, profile=profile, development=development)


def _build_app(project: Path, name: str, sysroot: Path | None = None, tools: Path | None = None,
              output: Path | None = None, *, offline: bool = True, jobs: int = 4,
              toolchain: str = EFI_TOOLCHAIN, manifest: Path | None = None,
              profile: str = "release", development: bool = False) -> dict:
    """Build a native ALPM app package against the distro's source-built sysroot."""
    project = Path(project).resolve()
    sysroot = Path(sysroot or project / "out/bootstrap/root").resolve(strict=True)
    tools = Path(tools or project / "out/bootstrap/root/tools").resolve(strict=True)
    output = Path(output or project / "out/apps").resolve()
    if not sysroot.is_relative_to(project / "out") or not tools.is_relative_to(project / "out"):
        raise RuntimeError("Application target compiler/sysroot must be managed distro outputs")
    applications = catalog(project)
    if name not in applications:
        raise RuntimeError(f"Unknown application package: {name}")
    app = applications[name]
    report = preflight(project, sysroot, tools=tools, names=[name])
    if not report["ready_to_build"]:
        raise RuntimeError("Missing desktop target build inputs: " + "; ".join(report["applications"][0]["missing_build_inputs"]))
    manifest = Path(manifest or output / "sources.json")
    source_manifest = json.loads(manifest.read_text()) if manifest.exists() else prepare(project, output)
    workspace = _verify_snapshot(source_manifest)
    rustup = shutil.which("rustup")
    rust_sysroot = subprocess.check_output([rustup, "run", toolchain, "rustc", "--print", "sysroot"], text=True).strip()
    rust_version = subprocess.check_output([rustup, "run", toolchain, "rustc", "--version", "--verbose"], text=True)
    rust_seed = {"version": rust_version, "compiler_sha256": sha256(Path(rust_sysroot) / "bin/rustc"),
                 "standard_library": {p.name: sha256(p) for p in sorted(
                     (Path(rust_sysroot) / "lib/rustlib" / RUST_TARGET / "lib").glob("*.rlib"))}}
    target_libraries = {library: sha256(Path(path)) for library, path in report["applications"][0]["libraries"].items()}
    sysroot_identity = _sysroot_identity(sysroot)
    compiler = tools / "pass2/bin" / f"{GNU_TARGET}-gcc"
    if not (tools / "pass2/.runtime-validated.json").is_file() or not compiler.is_file():
        compiler = tools / "bin" / f"{GNU_TARGET}-gcc"
    selected_source_identity = hashlib.sha256(json.dumps({
        "sources": {name: source_manifest["sources"][name]["identity"] for name in app["source"]["projects"]},
        "overlays": [record for record in source_manifest["overlays"]
                     if record["path"].split("/", 1)[0] in app["source"]["projects"]]}, sort_keys=True).encode()).hexdigest()
    identity = hashlib.sha256(json.dumps({"sources": selected_source_identity, "recipe": sha256(Path(app["recipe"])),
                                        "adapter": sha256(Path(__file__)), "target_libraries": target_libraries,
                                        "sysroot": sysroot_identity, "compiler": sha256(compiler),
                                        "libc": sha256(sysroot / ".bootstrap-validated.json"),
                                        "rust": rust_seed, "toolchain": toolchain,
                                        "profile": profile, "development": development,
                                        "incremental_adapter": sha256(Path(__file__).with_name("incremental.py"))}, sort_keys=True).encode()).hexdigest()
    work = output / "build" / name / identity
    work.mkdir(parents=True, exist_ok=True)
    state = project / "out/state" / ("apps-development" if development else "apps") / f"{name}.json"
    receipt = work / "result.json"
    if receipt.is_file():
        cached = json.loads(receipt.read_text())
        if cached.get("build_identity") == identity and Path(cached["path"]).is_file() and sha256(Path(cached["path"])) == cached.get("sha256"):
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps(cached, indent=2) + "\n")
            return cached
    # Toolchain/library changes isolate Cargo caches; ordinary source edits and
    # package revision changes reuse the same paths and dependency compilation.
    context = hashlib.sha256(json.dumps({"rust": rust_seed, "toolchain": toolchain,
        "compiler": sha256(compiler), "sysroot": sysroot_identity, "profile": profile,
        "adapter": sha256(Path(__file__)), "incremental_adapter": sha256(Path(__file__).with_name("incremental.py")),
        "build": app["build"], "requirements": app["requirements"]}, sort_keys=True).encode()).hexdigest()
    cache = output / "incremental" / name / context
    from .incremental import synchronize
    synchronize(workspace, cache / "workspace")
    workspace = cache / "workspace"
    adapter_snapshot = work / "adapter.py"
    adapter_snapshot.write_bytes(Path(__file__).read_bytes())
    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    wrapper = cache / "target-cc"
    _link_wrapper(wrapper, compiler, sysroot, [sysroot, tools, output, Path(rust_sysroot)])
    cpp = tools / "pass2/bin" / f"{GNU_TARGET}-g++"
    cpp_wrapper = cache / "target-cxx"
    if cpp.is_file() and (tools / "pass2/.runtime-validated.json").is_file():
        _link_wrapper(cpp_wrapper, cpp, sysroot, [sysroot, tools, output, Path(rust_sysroot)])
    cargo_home = output / "cargo-home"
    cargo_home.mkdir(parents=True, exist_ok=True)
    host_cargo = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")))
    for relative in ("registry/cache", "registry/index"):
        root = host_cargo / relative
        if root.is_dir():
            for registry in root.iterdir():
                if registry.is_dir() and (registry.name.startswith("index.crates.io-")
                        or registry.name == "github.com-1ecc6299db9ec823"):
                    shutil.copytree(registry, cargo_home / relative / registry.name, dirs_exist_ok=True,
                                    copy_function=lambda source, dest: shutil.copy2(source, dest)
                                    if not Path(dest).exists() else dest)
    env = _pc_environment(sysroot)
    env.update({"PATH": f"{tools / 'native/bin'}:{tools / 'bin'}:{Path(shutil.which('cargo')).parent}:/usr/bin:/bin",
                "CARGO_HOME": str(cargo_home), "CARGO_TARGET_DIR": str(cache / "target"),
                "CARGO_PROFILE_DEV_DEBUG": "0", "CARGO_PROFILE_DEV_INCREMENTAL": "true",
                "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
                "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER": str(wrapper),
                "CC_x86_64_unknown_linux_gnu": str(wrapper),
                "AR_x86_64_unknown_linux_gnu": str(tools / "bin" / f"{GNU_TARGET}-ar"),
                "HOST_CC": shutil.which("gcc") or "/usr/bin/gcc",
                "HOST_CXX": shutil.which("g++") or "/usr/bin/g++", "SOURCE_DATE_EPOCH": "1756684800",
                "TELORGON_WAYLAND_XML": str(sysroot / "usr/share/wayland/wayland.xml"),
                "TELORGON_WAYLAND_PROTOCOLS_DIR": str(sysroot / "usr/share/wayland-protocols"),
                "BINDGEN_EXTRA_CLANG_ARGS": f"--sysroot={sysroot} -target {RUST_TARGET} -isystem {sysroot / 'usr/include'}"})
    if cpp_wrapper.is_file():
        env["CXX_x86_64_unknown_linux_gnu"] = str(cpp_wrapper)
    if report["host_libclang"] and Path(report["host_libclang"]).is_absolute():
        env["LIBCLANG_PATH"] = str(Path(report["host_libclang"]).parent)
    rustflags = [f"--remap-path-prefix={workspace}=/src/telorgon-workspace",
                 "-Clink-arg=-Wl,--as-needed", "-Clink-arg=-Wl,-z,relro", "-Clink-arg=-Wl,-z,now"]
    for directory in ("usr/lib", "usr/lib64"):
        if (sysroot / directory).is_dir():
            rustflags.append(f"-Lnative={sysroot / directory}")
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(rustflags)
    command = [shutil.which("cargo"), f"+{toolchain}", "build", "--locked", "--profile", profile, "--target", RUST_TARGET,
               "--bin", app["build"]["binary"], "-j", str(jobs)]
    if offline:
        command.append("--offline")
    log = work / "build.log"
    with log.open("w") as stream:
        result = subprocess.run(command, cwd=workspace / app["source"]["primary"], env=env,
                                stdout=stream, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"Target application build failed ({result.returncode}); see {log}")
    _verify_snapshot(source_manifest)
    binary = work / app["build"]["binary"]
    shutil.copy2(cache / "target" / RUST_TARGET / ("debug" if profile == "dev" else "release") / app["build"]["binary"], binary)
    closure = _elf_closure(binary, sysroot)
    notices = _install(app, workspace, binary, stage, cargo_home, Path(rust_sysroot))
    provenance = {"kind": "source-built-application-with-declared-rust-seed", "build_identity": identity,
                  "sources": source_manifest, "compilation_source_identity": selected_source_identity,
                  "toolchain": toolchain, "rust_seed": rust_seed,
                  "adapter_snapshot": str(adapter_snapshot), "adapter_sha256": sha256(adapter_snapshot),
                  "rust_seed_sysroot": rust_sysroot, "target_compiler": str(compiler), "target_sysroot": str(sysroot),
                  "target_sysroot_identity": sysroot_identity,
                  "target_libraries": target_libraries, "elf": closure, "command": command, "log": str(log),
                  "license_notices": notices, "cargo_profile": profile, "cargo_cache": str(cache),
                  "runtime_qualification": "not established by compilation or ELF closure"}
    _write(stage, f"usr/share/custom-distro/provenance/{name}.json", json.dumps(provenance, indent=2) + "\n")
    from .packaging import export_package
    metadata = {**app["package"], "depends": app["runtime"]["packages"], "licenses": [app["package"]["license"]]}
    if development:
        metadata["version"] += ".dev" + identity
    artifact = export_package(stage, project / "out/packages" / name / identity, metadata, source_date_epoch=1756684800,
                              provenance=provenance)
    result = {"package": name, "build_identity": identity, "path": str(artifact.path),
              "sha256": sha256(artifact.path), "stage": str(stage), "binary": str(binary), "provenance": provenance}
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(result, indent=2) + "\n")
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    return result


def repackage_app(project: Path, name: str) -> dict:
    """Package a verified existing compilation with revised metadata/notices.

    Keep the original compile receipt rather than implying the current adapter
    compiled an older binary. This operation never changes application code.
    """
    project = project.resolve()
    state = project / "out/state/apps" / f"{name}.json"
    previous = json.loads(state.read_text())
    original = previous["provenance"]
    binary = Path(previous["binary"])
    sysroot = Path(original["target_sysroot"])
    workspace = _verify_snapshot(original["sources"])
    closure = _elf_closure(binary, sysroot)
    if closure != original["elf"] or _sysroot_identity(sysroot) != original["target_sysroot_identity"]:
        raise RuntimeError("Existing application compilation or target SDK differs from its receipt")
    app = catalog(project)[name]
    identity = hashlib.sha256(json.dumps({"compiled": original["build_identity"],
        "binary": sha256(binary), "recipe": sha256(Path(app["recipe"])),
        "packager": sha256(Path(__file__))}, sort_keys=True).encode()).hexdigest()
    work = project / "out/apps/repackage" / name / identity
    work.mkdir(parents=True, exist_ok=True)
    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    notices = _install(app, workspace, binary, stage, project / "out/apps/cargo-home",
                       Path(original["rust_seed_sysroot"]))
    packager = work / "packager.py"
    packager.write_bytes(Path(__file__).read_bytes())
    provenance = {**original, "build_identity": identity,
        "compilation_build_identity": original.get("compilation_build_identity", original["build_identity"]),
        "packaging": {"operation": "verified-binary-metadata-and-notice-export",
                      "packager_snapshot": str(packager), "packager_sha256": sha256(packager),
                      "recipe_sha256": sha256(Path(app["recipe"])),
                      "original_package_sha256": previous["sha256"]}, "license_notices": notices}
    _write(stage, f"usr/share/custom-distro/provenance/{name}.json", json.dumps(provenance, indent=2) + "\n")
    from .packaging import export_package
    metadata = {**app["package"], "depends": app["runtime"]["packages"], "licenses": [app["package"]["license"]]}
    artifact = export_package(stage, project / "out/packages" / name / identity, metadata,
                              source_date_epoch=1756684800, provenance=provenance)
    result = {"package": name, "build_identity": identity, "path": str(artifact.path),
              "sha256": sha256(artifact.path), "stage": str(stage), "binary": str(binary), "provenance": provenance}
    temporary = state.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(state)
    return result
