"""Source-built Telorgon EFI loader and distro-owned early userspace.

All build writes stay beneath the supplied output directory. No helper changes
the running kernel, mounts a filesystem, or writes firmware variables.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile


BOOT_MARKER = "CUSTOM_DISTRO_BOOT_OK"
EFI_TOOLCHAIN = "nightly-2026-10-06"
EFI_TARGET = "x86_64-unknown-uefi"
MAX_BOOT_BYTES = 512 * 1024 * 1024


def sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_efi(path: Path) -> dict:
    """Require an x86-64 PE32+ EFI application, including Linux EFI stubs."""
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BOOT_BYTES:
        raise RuntimeError(f"Missing or oversized EFI image: {path}")
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise RuntimeError(f"EFI image lacks DOS/PE header: {path}")
        offset = struct.unpack_from("<I", header, 0x3C)[0]
        if offset < 64 or offset + 94 > path.stat().st_size:
            raise RuntimeError(f"Invalid EFI header offset: {path}")
        stream.seek(offset)
        pe = stream.read(94)
    if (pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664
            or struct.unpack_from("<H", pe, 24)[0] != 0x20B
            or struct.unpack_from("<H", pe, 92)[0] != 10):
        raise RuntimeError(f"Expected an x86-64 PE32+ EFI application: {path}")
    return {"path": str(path.resolve()), "sha256": sha256(path), "size": path.stat().st_size}


def _git_metadata(source: Path) -> dict:
    result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        return {"git_head": None, "git_dirty": None}
    status = subprocess.run(["git", "-C", str(source), "status", "--porcelain"],
                            capture_output=True, text=True, check=False)
    return {"git_head": result.stdout.strip(), "git_dirty": bool(status.stdout.strip())}


def _snapshot(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    if not source.is_dir() or destination.resolve().is_relative_to(source):
        raise RuntimeError("Source must be a directory outside the build output")
    destination.mkdir(parents=True)
    files = []
    excluded = {".git", "target", "vendor", "out", "__pycache__", ".cache"}
    for directory, directories, names in os.walk(source, followlinks=False):
        base = Path(directory)
        directories[:] = sorted(name for name in directories if name not in excluded)
        for name in list(directories):
            if (base / name).is_symlink():
                raise RuntimeError(f"Source directory symlink needs an explicit source rule: {base / name}")
        for name in sorted(names):
            if name in excluded:
                continue
            path = base / name
            relative = path.relative_to(source)
            if path.is_symlink():
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(source) or not resolved.is_file():
                    raise RuntimeError(f"Source symlink escapes source tree: {path}")
            elif not path.is_file():
                raise RuntimeError(f"Unsupported source file type: {path}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=True)
            files.append({"path": relative.as_posix(), "sha256": sha256(target),
                          "mode": stat.S_IMODE(target.stat().st_mode), "size": target.stat().st_size})
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"source": str(source), "sha256": digest, "files": files, **_git_metadata(source)}


def boot_config(name: str = "Custom Distro", *, target: str = "custom-distro",
                arguments: str | None = None, autoboot: bool = True, uki: bool = False) -> str:
    if not name or any(c in name for c in "\n\r\0"):
        raise RuntimeError("Distro name must be nonempty and fit on one line")
    if target not in {"custom-distro", "recovery"}:
        raise RuntimeError("Unsupported boot target")
    directory = "Custom" if target == "custom-distro" else "Recovery"
    image = rf"\EFI\{directory}\{'linux.efi' if uki else 'kernel.efi'}"
    if arguments is None and not uki:
        arguments = rf"initrd=\EFI\{directory}\initrd.img console=tty0 console=ttyS0,115200 panic=0"
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ['theme = "disks"', f'selection = "{"auto" if autoboot else "always"}"',
             f"default = {quote(target)}", "", "[[targets]]", f"id = {quote(target)}",
             f"name = {quote(name)}", 'kind = "linux"', 'detail = "Source-built Linux prototype"',
             f"image = {quote(image)}", "embedded_splash = false"]
    if arguments:
        lines.append(f"arguments = {quote(arguments)}")
    return "\n".join(lines) + "\n"


def build_loader(loader_source: Path, framework_source: Path, output: Path, *,
                 toolchain: str = EFI_TOOLCHAIN, offline: bool = True,
                 config: Path | None = None) -> dict:
    """Snapshot local sources, record their bytes, and compile a fresh EFI image.

    Dirty/local sources are recorded as such. This provenance is a reproducible
    local input record, not a claim of an upstream signed release.
    """
    source_bundle = None
    bundle_root = Path(loader_source).resolve().parent
    if (bundle_root / "source-manifest.json").is_file():
        from .source_bundle import verify_workspace
        origin = verify_workspace(bundle_root)
        if Path(framework_source).resolve() != bundle_root / "telorgon":
            raise RuntimeError("bundled EFI inputs must come from the same verified workspace")
        source_bundle = {"identity": origin["identity"],
                         "manifest_sha256": sha256(bundle_root / "source-manifest.json")}
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace = output / "sources" / "telorgon-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="source-", dir=workspace.parent))
    try:
        sources = {"telorgon": _snapshot(Path(framework_source), temporary / "telorgon"),
                   "telorgon-bootloader": _snapshot(Path(loader_source), temporary / "telorgon-bootloader")}
        text = Path(config).read_text() if config else boot_config()
        embedded = temporary / "telorgon-bootloader/crates/telorgon-boot-efi/src/boot.toml"
        embedded.write_text(text, encoding="utf-8")
        overlay = {"path": "telorgon-bootloader/crates/telorgon-boot-efi/src/boot.toml",
                   "sha256": sha256(embedded)}
        identity = hashlib.sha256(json.dumps({"sources": {k: v["sha256"] for k, v in sources.items()},
                                             "overlay": overlay, "toolchain": toolchain},
                                            sort_keys=True).encode()).hexdigest()
        snapshot = workspace / identity
        if snapshot.exists():
            shutil.rmtree(temporary)
        else:
            temporary.rename(snapshot)
        manifest = output / "loader-sources.json"
        manifest.write_text(json.dumps({"identity": identity, "kind": "local-content-snapshot",
                                        "sources": sources, "overlay": overlay, "toolchain": toolchain,
                                        "source_bundle": source_bundle},
                                       indent=2) + "\n")
        cargo = shutil.which("cargo")
        if not cargo:
            raise RuntimeError("Cargo is required to build the Telorgon EFI application")
        target_dir = output / "cargo-target"
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = str(target_dir)
        # Cargo still writes extraction state and cache locks in offline mode.
        # Seed only public registry indexes/archives; credentials and the host's
        # mutable cache database never enter this private Cargo home.
        host_cargo = Path(env.get("CARGO_HOME", str(Path.home() / ".cargo")))
        private_cargo = output / "cargo-home"
        private_cargo.mkdir(exist_ok=True)
        for cache in ("registry/cache", "registry/index"):
            seed = host_cargo / cache
            if seed.is_dir():
                shutil.copytree(seed, private_cargo / cache, dirs_exist_ok=True)
        env["CARGO_HOME"] = str(private_cargo)
        command = [cargo, f"+{toolchain}", "build", "--locked", "--release", "--target", EFI_TARGET,
                   "-p", "telorgon-boot-efi"]
        if offline:
            command.append("--offline")
        log = output / "loader-build.log"
        with log.open("w") as stream:
            result = subprocess.run(command, cwd=snapshot / "telorgon-bootloader", env=env,
                                    stdout=stream, stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            raise RuntimeError(f"Telorgon EFI source build failed ({result.returncode}); see {log}")
        artifact = target_dir / EFI_TARGET / "release/boot-efi.efi"
        validated = validate_efi(artifact)
        return {**validated, "artifact": str(artifact), "source_manifest": str(manifest),
                "source_identity": identity, "log": str(log), "toolchain": toolchain,
                "source_bundle": source_bundle,
                "command": command, "cargo_home": str(private_cargo),
                "cargo_seed": "public-host-registry-cache"}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _newc_record(stream, name: bytes, mode: int, content: bytes, inode: int, *, mtime: int) -> None:
    fields = (inode, mode, 0, 0, 1, mtime, len(content), 0, 0, 0, 0, len(name) + 1, 0)
    header = b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
    stream.write(header + name + b"\0")
    stream.write(b"\0" * (-(len(header) + len(name) + 1) % 4))
    stream.write(content)
    stream.write(b"\0" * (-len(content) % 4))


def build_initramfs(root: Path, output: Path, *, epoch: int = 0) -> Path:
    """Write a deterministic Linux newc archive without privileged device nodes."""
    output = Path(output).absolute()
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise RuntimeError("Initramfs output must be an ordinary file")
    root, output = Path(root).resolve(strict=True), output.resolve()
    if not root.is_dir() or output.is_relative_to(root):
        raise RuntimeError("Initramfs output must be outside its input root")
    if not (root / "init").is_file() or not os.access(root / "init", os.X_OK):
        raise RuntimeError("Early userspace requires an executable /init")
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise RuntimeError("Initramfs timestamp must fit in an unsigned 32-bit integer")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=output.name + ".partial-", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    total = 0
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                paths = [root, *sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())]
                for inode, path in enumerate(paths, 1):
                    info = path.lstat()
                    relative = path.relative_to(root).as_posix() if path != root else "."
                    name = os.fsencode(relative)
                    if b"\0" in name or name.startswith(b"/") or b".." in name.split(b"/"):
                        raise RuntimeError(f"Unsafe initramfs member: {relative}")
                    if stat.S_ISDIR(info.st_mode):
                        content = b""
                    elif stat.S_ISLNK(info.st_mode):
                        content = os.fsencode(os.readlink(path))
                    elif stat.S_ISREG(info.st_mode):
                        total += info.st_size
                        if total > MAX_BOOT_BYTES:
                            raise RuntimeError("Initramfs exceeds the prototype's 512 MiB limit")
                        content = path.read_bytes()
                    else:
                        raise RuntimeError(f"Unsupported initramfs member type: {relative}")
                    _newc_record(compressed, name, info.st_mode, content, inode, mtime=epoch)
                _newc_record(compressed, b"TRAILER!!!", 0, b"", len(paths) + 1, mtime=epoch)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def stage_esp(loader: Path, kernel: Path, initramfs: Path | None, esp: Path, *,
              name: str = "Custom Distro", root_uuid: str | None = None,
              autoboot: bool = True, uki: bool = False) -> dict:
    """Stage normal boot files on a private directory representing an EFI volume."""
    loader_info, kernel_info = validate_efi(Path(loader)), validate_efi(Path(kernel))
    esp = Path(esp).resolve()
    if esp.exists() and any(esp.iterdir()):
        raise RuntimeError(f"ESP staging directory must be empty: {esp}")
    for directory in ("EFI/BOOT", "EFI/Telorgon", "EFI/Custom"):
        (esp / directory).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(loader, esp / "EFI/BOOT/BOOTX64.EFI")
    shutil.copyfile(loader, esp / "EFI/Telorgon/loader.efi")
    shutil.copyfile(kernel, esp / f"EFI/Custom/{'linux.efi' if uki else 'kernel.efi'}")
    arguments = None
    if not uki:
        if initramfs is None or not Path(initramfs).is_file():
            raise RuntimeError("EFI kernel boot requires its distro-built initramfs")
        shutil.copyfile(initramfs, esp / "EFI/Custom/initrd.img")
        arguments = r"initrd=\EFI\Custom\initrd.img console=tty0 console=ttyS0,115200 panic=0"
        if root_uuid:
            if any(c not in "0123456789abcdefABCDEF-" for c in root_uuid):
                raise RuntimeError("Root UUID contains invalid characters")
            arguments += f" root=UUID={root_uuid} rw"
    configuration = esp / "EFI/Telorgon/boot.toml"
    configuration.write_text(boot_config(name, arguments=arguments, autoboot=autoboot, uki=uki),
                             encoding="utf-8")
    return {"esp": str(esp), "loader": loader_info, "kernel": kernel_info,
            "initramfs": None if uki else {"path": str(Path(initramfs).resolve()),
                                           "sha256": sha256(Path(initramfs))},
            "config": str(configuration), "payload_kind": "uki" if uki else "efi-kernel-initramfs"}
