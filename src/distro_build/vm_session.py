"""Normal desktop VMs with private persistent disks and firmware."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .boot import sha256
from .media import validate_disk
from .vm import discover_runtime


def prepare(project: Path, image: Path, name: str, runtime: dict) -> tuple[Path, dict]:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}", name):
        raise RuntimeError("VM name must contain only letters, numbers, underscores or hyphens")
    parent = project / "out/vms"
    if parent.is_symlink():
        raise RuntimeError("Persistent VM parent cannot be a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / name
    if directory.is_symlink():
        raise RuntimeError("Persistent VM directory cannot be a symlink")
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    for filename in ("disk.img", "OVMF_VARS.fd", "state.json", "vm.lock", "serial.log", "qemu.log", "command.json", "session.json", "qmp.sock", "agent.sock", "deploy.lock", "deployment.json"):
        if (directory / filename).is_symlink():
            raise RuntimeError("Persistent VM files cannot be symlinks")
    lock = directory / "vm.lock"
    # The caller holds this lock while preparing and using the writable disk.
    return directory, {"lock": lock}


def initialize(directory: Path, image: Path, runtime: dict) -> dict:
    state = directory / "state.json"
    disk = directory / "disk.img"
    variables = directory / "OVMF_VARS.fd"
    if any(path.is_symlink() for path in (state, disk, variables)):
        raise RuntimeError("Persistent VM files cannot be symlinks")
    if state.exists():
        record = json.loads(state.read_text())
        if not isinstance(record, dict) or record.get("schema") != 1 or not disk.is_file() or not variables.is_file():
            raise RuntimeError("Persistent VM state is incomplete; preserve it and select another --name")
        if (record.get("disk") != str(disk) or record.get("firmware_variables") != str(variables)
                or not isinstance(record.get("base_image"), str)
                or not isinstance(record.get("firmware_code"), str)
                or any(not isinstance(record.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", record[key])
                       for key in ("base_image_sha256", "firmware_code_sha256"))):
            raise RuntimeError("Persistent VM state has invalid identities or private paths")
        validate_disk(disk)
        if sha256(Path(runtime["code"])) != record["firmware_code_sha256"]:
            raise RuntimeError("This VM needs its original matching OVMF code; preserve it and select another --name")
        return record
    if disk.exists() or variables.exists():
        raise RuntimeError("Unrecorded VM disk/firmware exists; preserve it and select another --name")
    image = image.resolve(strict=True)
    validate_disk(image)
    image_digest = sha256(image)
    # Bind a managed alias to its immutable content object before copying it.
    retained = image.parent / "objects" / f"{image_digest}.img"
    if retained.is_file():
        if retained.is_symlink() or sha256(retained) != image_digest:
            raise RuntimeError("Managed VM source differs from its content identity")
        image = retained
    temporary = directory / "disk.next"
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError("Incomplete VM preparation exists; preserve it and select another --name")
    shutil.copyfile(image, temporary)
    temporary.chmod(0o600)
    if sha256(temporary) != image_digest:
        temporary.unlink()
        raise RuntimeError("VM disk copy differs from its source")
    temporary.replace(disk)
    shutil.copyfile(runtime["vars"], variables)
    variables.chmod(0o600)
    record = {"schema": 1, "base_image": str(image), "base_image_sha256": image_digest,
              "firmware_code": runtime["code"], "firmware_code_sha256": sha256(Path(runtime["code"])),
              "disk": str(disk), "firmware_variables": str(variables),
              "scope": "Normal desktop use; writable private disk and firmware retained between launches"}
    state.write_text(json.dumps(record, indent=2) + "\n")
    state.chmod(0o600)
    return record


def start(project: Path, image: Path, *, name: str = "custom", display: str = "gtk,gl=off,zoom-to-fit=off", development: bool = False,
          audio: str = "auto", usb_camera: str | None = None) -> dict:
    project = project.resolve()
    runtime = discover_runtime(project)
    from .vm_media import audio_options, camera_options
    directory, paths = prepare(project, image, name, runtime)
    with paths["lock"].open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("This VM is already running; use another --name for a separate VM") from error
        audio_arguments, audio_record = audio_options(runtime, audio)
        camera_arguments, camera_record = camera_options(usb_camera)
        if audio_record["backend"] == "none" and audio != "none":
            print("No host PulseAudio endpoint found; VM sound uses a silent backend. "
                  "Set PULSE_SERVER and use --audio pulse for host playback and microphone input.", file=sys.stderr)
        record = initialize(directory, image, runtime)
        qmp = directory / "qmp.sock"
        agent = directory / "agent.sock"
        if len(os.fsencode(agent)) >= 104:
            raise RuntimeError("VM path is too long for its private agent socket")
        agent.unlink(missing_ok=True)
        if qmp.exists():
            # The lock proves no managed session is active. A stale socket can
            # remain after an interrupted host process; QEMU cannot reuse it.
            qmp.unlink()
        env = runtime["environment"].copy()
        env["TMPDIR"] = str(directory)
        acceleration = "tcg"
        acceleration_failure = "/dev/kvm is missing or this process lacks read/write access"
        if os.access("/dev/kvm", os.R_OK | os.W_OK):
            probe = subprocess.run([runtime["qemu"], "-machine", "none", "-accel", "kvm", "-nodefaults",
                                    "-display", "none", "-monitor", "stdio", "-S"],
                                   input="quit\n", text=True, capture_output=True, env=env, timeout=10)
            if not probe.returncode:
                acceleration = "kvm"
                acceleration_failure = None
            else:
                acceleration_failure = probe.stderr.strip() or f"KVM probe exited with status {probe.returncode}"
        print(f"VM acceleration: {acceleration.upper()}", file=sys.stderr, flush=True)
        if acceleration == "tcg":
            print(f"Warning: using slow CPU emulation because KVM is unavailable: {acceleration_failure}. "
                  "If your account was just added to the kvm group, open a new login session or run "
                  "'newgrp kvm' before launching again.", file=sys.stderr, flush=True)
        for path in (directory, Path(runtime["code"])):
            if any(char in str(path) for char in ",\n\r"):
                raise RuntimeError("QEMU paths cannot contain commas or newlines")
        command = [runtime["qemu"], "-name", "Custom Distro", "-machine", "q35", "-accel",
                   "kvm" if acceleration == "kvm" else "tcg,thread=multi", "-cpu",
                   "host" if acceleration == "kvm" else "max", "-smp", "8", "-m", "2048",
                   "-nodefaults", "-device", "virtio-vga,xres=1280,yres=720", "-display", display,
                   "-serial", f"file:{directory / 'serial.log'}", "-monitor", "none",
                   "-qmp", f"unix:{qmp},server=on,wait=off",
                   "-drive", f"if=pflash,format=raw,readonly=on,file={runtime['code']}",
                   "-drive", f"if=pflash,format=raw,file={directory / 'OVMF_VARS.fd'}",
                   "-drive", f"id=bootdisk,if=none,format=raw,file={directory / 'disk.img'}",
                   "-device", "ide-hd,drive=bootdisk,bus=ide.0,bootindex=1",
                   "-object", "rng-random,id=entropy,filename=/dev/urandom",
                   "-device", "virtio-rng-pci,rng=entropy",
                   "-device", "qemu-xhci,id=xhci", "-device", "usb-kbd,bus=xhci.0",
                   "-device", "usb-tablet,bus=xhci.0", "-netdev", "user,id=network",
                   "-device", "virtio-net-pci,netdev=network"]
        command += audio_arguments + camera_arguments
        if runtime["data"]:
            command += ["-L", runtime["data"]]
        if development:
            command += ["-device", "virtio-serial-pci,id=development",
                        "-chardev", f"socket,path={agent},server=on,wait=off,id=development-agent",
                        "-device", "virtserialport,bus=development.0,chardev=development-agent,name=org.qemu.guest_agent.0"]
        (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        print(f"Opening Custom Distro. Your files and settings are saved in {directory / 'disk.img'}. Close QEMU to stop.", file=sys.stderr, flush=True)
        with (directory / "qemu.log").open("w") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, umask=0o077)
            try:
                process.wait()
            except KeyboardInterrupt:
                process.terminate()
                process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                qmp.unlink(missing_ok=True)
                agent.unlink(missing_ok=True)
        result = {"mode": "normal-desktop-use", "name": name, "disk": record["disk"],
                  "base_image_sha256": record["base_image_sha256"], "exit_code": process.returncode,
                  "persistent": True, "boot_assertions": False, "network": "qemu-user-nat",
                  "directory": str(directory), "development_agent": development}
        result.update(acceleration=acceleration, acceleration_failure=acceleration_failure)
        result.update(audio=audio_record, camera=camera_record)
        (directory / "session.json").write_text(json.dumps(result, indent=2) + "\n")
        if process.returncode not in (0, -15):
            raise RuntimeError(f"QEMU exited with status {process.returncode}; see {directory / 'qemu.log'}")
        return result
