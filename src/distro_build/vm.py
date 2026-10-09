"""Bounded, headless UEFI boot checks with private firmware and QMP control."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

from .boot import BOOT_MARKER, sha256
from .media import validate_disk


def discover_runtime(project: Path | None = None) -> dict:
    """Prefer explicit/system tools; accept the existing read-only local VM seed."""
    project = Path(project or Path(__file__).resolve().parents[2]).resolve()
    local = project.parent / "telorgon-bootloader/target/boot-vm/qemu-runtime"
    supplied = os.environ.get("QEMU_SYSTEM_X86_64")
    qemu = shutil.which(supplied or "qemu-system-x86_64")
    local_runtime = False
    if qemu is None and not supplied and (local / "usr/bin/qemu-system-x86_64").is_file():
        qemu = str(local / "usr/bin/qemu-system-x86_64")
        local_runtime = True
    if not qemu:
        raise RuntimeError("QEMU is missing; set QEMU_SYSTEM_X86_64 to an existing host tool")
    code, variables = os.environ.get("OVMF_CODE"), os.environ.get("OVMF_VARS")
    if bool(code) != bool(variables):
        raise RuntimeError("OVMF_CODE and OVMF_VARS must identify a matching firmware pair")
    if not code:
        directories = [local / "usr/share/OVMF"] if local_runtime else []
        directories += [Path(p) for p in ("/usr/share/OVMF", "/usr/share/edk2/ovmf",
                                         "/usr/share/edk2/x64", "/usr/share/edk2-ovmf/x64", "/usr/share/qemu")]
        for directory in directories:
            for suffix in ("_4M", ".4m", ""):
                candidate_code = directory / f"OVMF_CODE{suffix}.fd"
                candidate_variables = directory / f"OVMF_VARS{suffix}.fd"
                if candidate_code.is_file() and candidate_variables.is_file():
                    code, variables = str(candidate_code), str(candidate_variables)
                    break
            if code:
                break
    if not code or not variables or not Path(code).is_file() or not Path(variables).is_file():
        raise RuntimeError("Unsigned OVMF is missing; set OVMF_CODE and OVMF_VARS")
    environment = os.environ.copy()
    data = os.environ.get("QEMU_DATA_DIR")
    if local_runtime:
        environment["LD_LIBRARY_PATH"] = str(local / "usr/lib/x86_64-linux-gnu")
        environment["QEMU_MODULE_DIR"] = str(local / "usr/lib/x86_64-linux-gnu/qemu")
        data = data or str(local / "usr/share/qemu")
    return {"qemu": str(Path(qemu).resolve()), "code": str(Path(code).resolve()),
            "vars": str(Path(variables).resolve()), "data": data, "environment": environment,
            "kind": "read-only-local-seed" if local_runtime else "host-tools"}


class Qmp:
    """One synchronous QMP connection; events are recorded while awaiting replies."""

    def __init__(self, path: Path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(3)
        self.events = []
        self.identifier = 0
        try:
            self.socket.connect(str(path))
            self.stream = self.socket.makefile("rwb", buffering=0)
            greeting = json.loads(self.stream.readline())
            if "QMP" not in greeting:
                raise RuntimeError("QEMU did not supply a QMP greeting")
            self.execute("qmp_capabilities")
        except Exception:
            self.close()
            raise

    def execute(self, command: str, arguments: dict | None = None):
        self.identifier += 1
        request = {"execute": command, "id": self.identifier}
        if arguments is not None:
            request["arguments"] = arguments
        self.stream.write(json.dumps(request).encode() + b"\n")
        while True:
            line = self.stream.readline()
            if not line:
                raise RuntimeError("QMP connection closed before its reply")
            reply = json.loads(line)
            if "event" in reply:
                self.events.append(reply)
                continue
            if reply.get("id") != self.identifier:
                continue
            if "error" in reply:
                raise RuntimeError(f"QMP {command} failed: {reply['error']}")
            return reply.get("return")

    def key(self, key: str = "ret") -> None:
        self.execute("send-key", {"keys": [{"type": "qcode", "data": key}], "hold-time": 100})

    def close(self) -> None:
        if hasattr(self, "stream"):
            self.stream.close()
        self.socket.close()


def _read_log(path: Path, limit: int = 32 * 1024 * 1024) -> str:
    if not path.exists():
        return ""
    if path.stat().st_size > limit:
        raise RuntimeError(f"Guest log exceeded the {limit // (1024 * 1024)} MiB limit")
    return path.read_text(encoding="utf-8", errors="replace")


def observed_marker(text: str, marker: str) -> bool:
    """Accept a completed guest assertion line, never quoted manifest text."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    for line in text.replace("\r", "\n").split("\n")[:-1]:
        line = line.strip()
        prefixed = re.fullmatch(r"[A-Za-z0-9_.@-]+\[[0-9]+\]:\s+(.*)", line)
        if (prefixed.group(1) if prefixed else line) == marker:
            return True
    return False


def success_markers(expect: str, desktop_input: bool) -> list[str]:
    """Retain the desktop evidence before stopping asynchronous serial output."""
    markers = [expect]
    if desktop_input:
        markers += [
            "CUSTOM_DESKTOP_WINDOW_INPUT_OK configured=1 initial_presented=1 keyboard_focus=1 key_pressed=1 redraw_presented=1",
            "CUSTOM_WAYLAND_LOG_END name=files",
            "CUSTOM_WAYLAND_LOG_END name=settings",
            "CUSTOM_DESKTOP_USER_OK",
        ]
    return markers


def observed_success(text: str, markers: list[str], *, desktop_input: bool,
                     input_sent: bool) -> bool:
    return (not desktop_input or input_sent) and all(observed_marker(text, marker) for marker in markers)


def run(image: Path, output: Path, *, timeout: float = 180,
        expect: str = BOOT_MARKER, headless: bool = True, cpus: int = 2,
        memory_mib: int = 1024, acceleration: str = "auto",
        menu_boot: bool = False, project: Path | None = None, desktop_input: bool = False,
        interactive: bool = False) -> dict:
    """Boot only a private virtual disk and stop after the expected userspace marker.

    A receipt confirms the requested guest assertions. Desktop input mode also
    retains complete window logs for the mandatory host protocol checker.
    Installation, update lifecycle and hardware support have separate gates.
    Interactive runs keep QEMU open after successful startup; the timeout
    applies only to startup, and changes remain in the disposable snapshot.
    """
    image = Path(image).resolve(strict=True)
    validate_disk(image)
    image_digest = sha256(image)
    requested_image = image
    # Managed aliases can move while a later image is assembled. Execute the
    # retained, read-only content object associated with the measured digest.
    if project is not None:
        retained = Path(project).resolve() / "out/images/objects" / f"{image_digest}.img"
        if retained.exists():
            if retained.is_symlink() or sha256(retained) != image_digest:
                raise RuntimeError("Retained VM image differs from its content identity")
            image = retained
    output = Path(output).absolute()
    if output.is_symlink():
        raise RuntimeError("VM output directory cannot be a symlink")
    output = output.resolve()
    if output.is_relative_to(image):
        raise RuntimeError("VM output must be a directory separate from the image")
    if not 1 <= timeout <= 3600 or not 1 <= cpus <= 64 or not 128 <= memory_mib <= 32768:
        raise RuntimeError("Invalid VM timeout, CPU count or memory size")
    if not expect or len(expect) > 1024 or any(c in expect for c in "\r\n\x1b"):
        raise RuntimeError("VM success marker must be a nonempty short string")
    runtime = discover_runtime(project)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("qmp.sock", "OVMF_VARS.fd", "serial.log", "qemu.log", "command.json", "result.json", "screen.ppm"):
        if (output / name).is_symlink():
            raise RuntimeError(f"VM output cannot overwrite a symlink: {output / name}")
    qmp_path = output / "qmp.sock"
    if len(os.fsencode(qmp_path)) >= 104:
        raise RuntimeError("VM output path is too long for its private QMP socket")
    if qmp_path.exists():
        raise RuntimeError(f"A prior VM socket exists; choose a new run directory: {qmp_path}")
    private_variables = output / "OVMF_VARS.fd"
    shutil.copyfile(runtime["vars"], private_variables)
    serial, qemu_log = output / "serial.log", output / "qemu.log"
    serial.write_text("")
    env = runtime["environment"].copy()
    env["TMPDIR"] = str(output)
    if acceleration == "auto":
        acceleration = "tcg"
        if os.access("/dev/kvm", os.R_OK | os.W_OK):
            probe = subprocess.run([runtime["qemu"], "-machine", "none", "-accel", "kvm",
                                    "-nodefaults", "-display", "none", "-monitor", "stdio", "-S"],
                                   input="quit\n", text=True, capture_output=True, env=env, timeout=10)
            if not probe.returncode:
                acceleration = "kvm"
    if acceleration not in {"kvm", "tcg"}:
        raise RuntimeError("VM acceleration must be auto, kvm or tcg")
    for path in (image, output, Path(runtime["code"]), private_variables):
        if any(c in str(path) for c in ",\n\r"):
            raise RuntimeError("QEMU image/firmware paths cannot contain commas or newlines")
    command = [runtime["qemu"], "-name", "Custom Distro boot check", "-machine", "q35",
               "-accel", "kvm" if acceleration == "kvm" else "tcg,thread=multi",
               "-cpu", "host" if acceleration == "kvm" else "max", "-smp", str(cpus),
               "-m", str(memory_mib), "-nodefaults", "-vga", "std", "-no-reboot",
               "-display", "none" if headless else "gtk,gl=off", "-serial", f"file:{serial}",
               "-monitor", "none", "-qmp", f"unix:{qmp_path},server=on,wait=off", "-nic", "none",
               "-drive", f"if=pflash,format=raw,readonly=on,file={runtime['code']}",
               "-drive", f"if=pflash,format=raw,file={private_variables}",
               "-drive", f"id=bootdisk,if=none,format=raw,snapshot=on,file={image}",
               "-device", "ide-hd,drive=bootdisk,bus=ide.0,bootindex=1",
               "-object", "rng-random,id=entropy,filename=/dev/urandom",
               "-device", "virtio-rng-pci,rng=entropy",
               "-device", "qemu-xhci,id=xhci", "-device", "usb-kbd,bus=xhci.0"]
    if runtime["data"]:
        command += ["-L", runtime["data"]]
    if interactive:
        command += ["-device", "usb-tablet,bus=xhci.0"]
    (output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    connection = None
    started = time.monotonic()
    success = False
    error = None
    screenshot_error = None
    events = []
    last_key = 0.0
    input_sent = False
    required_markers = success_markers(expect, desktop_input)
    with qemu_log.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        try:
            deadline = started + timeout
            while time.monotonic() < deadline:
                if connection is None and qmp_path.exists():
                    try:
                        connection = Qmp(qmp_path)
                        error = None
                    except (OSError, RuntimeError) as failure:
                        error = str(failure)
                text = _read_log(serial)
                if "Kernel panic - not syncing:" in text:
                    error = "Guest kernel panicked; inspect the serial log"
                    break
                if observed_success(text, required_markers, desktop_input=desktop_input, input_sent=input_sent):
                    success = True
                    break
                if expect.endswith("_OK") and observed_marker(text, expect[:-3] + "_FAILED"):
                    error = "Guest runtime assertion failed; inspect the serial log"
                    break
                if process.poll() is not None:
                    error = f"QEMU exited before the userspace marker (status {process.returncode})"
                    break
                if desktop_input and not input_sent and connection is not None and observed_marker(text, "CUSTOM_DESKTOP_PROBE_AWAIT_INPUT key=16"):
                    connection.key("q")
                    input_sent = True
                elapsed = time.monotonic() - started
                if menu_boot and connection is not None and elapsed >= 20 and elapsed - last_key >= 10:
                    connection.key()
                    last_key = elapsed
                time.sleep(0.2)
            if not success and error is None:
                error = f"Userspace marker was not observed within {timeout:g} seconds"
            if connection is not None and process.poll() is None:
                try:
                    connection.execute("screendump", {"filename": str(output / "screen.ppm")})
                except (OSError, RuntimeError) as failure:
                    screenshot_error = str(failure)
                if interactive and success:
                    print("Custom Distro is ready. Close the QEMU window or press Ctrl+C to stop. Changes are temporary.", file=sys.stderr, flush=True)
                    try:
                        process.wait()
                    except KeyboardInterrupt:
                        pass
                try:
                    if process.poll() is None:
                        connection.execute("quit")
                except (OSError, RuntimeError):
                    pass
                events = connection.events
        except KeyboardInterrupt:
            error = "VM stopped by the user before startup verification completed"
            if connection is not None and process.poll() is None:
                try:
                    connection.execute("quit")
                except (OSError, RuntimeError):
                    pass
        finally:
            if connection is not None:
                connection.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            qmp_path.unlink(missing_ok=True)
    # The guest can power off just before polling catches its final serial output.
    final_text = _read_log(serial)
    success = observed_success(final_text, required_markers, desktop_input=desktop_input, input_sent=input_sent) and "Kernel panic - not syncing:" not in final_text
    result = {"success": success, "marker": expect, "elapsed_seconds": round(time.monotonic() - started, 2),
              "assertion_format": "completed-serial-line-v1", "checker_sha256": sha256(Path(__file__)),
              "exit_code": process.returncode, "image": str(image), "requested_image": str(requested_image), "image_sha256": image_digest, "serial_log": str(serial),
              "qemu_log": str(qemu_log), "screenshot": str(output / "screen.ppm"),
              "runtime_kind": runtime["kind"], "acceleration": acceleration,
              "desktop_input_sent": input_sent,
              "interactive": interactive,
              "required_markers": required_markers,
              "private_firmware_variables": str(private_variables), "events": events,
              "error": None if success else error, "screenshot_error": screenshot_error}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
