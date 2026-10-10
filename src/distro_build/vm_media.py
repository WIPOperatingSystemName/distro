"""Explicit host media endpoints for the private desktop VM."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess


def audio_options(runtime: dict, mode: str) -> tuple[list[str], dict]:
    if mode not in {"auto", "pulse", "none"}:
        raise RuntimeError("VM audio must be auto, pulse or none")
    server = runtime["environment"].get("PULSE_SERVER")
    if not server:
        directory = runtime["environment"].get("XDG_RUNTIME_DIR")
        candidate = Path(directory) / "pulse/native" if directory else Path("/mnt/wslg/PulseServer")
        if candidate.is_socket():
            server = "unix:" + str(candidate)
    if server and any(char in server for char in ",\r\n"):
        raise RuntimeError("PulseAudio server cannot contain commas or newlines")
    available = subprocess.run([runtime["qemu"], "-audiodev", "help"],
        env=runtime["environment"], text=True, capture_output=True, check=True, timeout=10).stdout.split()
    backend = "pa" if mode == "pulse" or mode == "auto" and server and "pa" in available else "none"
    if backend == "pa" and "pa" not in available:
        raise RuntimeError("This QEMU lacks the PulseAudio audio backend")
    argument = backend + ",id=hostaudio"
    if backend == "pa" and server:
        argument += ",server=" + server
    return ["-audiodev", argument, "-device", "intel-hda,id=sound",
            "-device", "hda-duplex,bus=sound.0,audiodev=hostaudio"], {
        "requested": mode, "backend": backend, "playback_and_capture": backend != "none",
    }


def camera_options(selector: str | None, *, usb_root: Path = Path("/dev/bus/usb"),
                   sysfs_root: Path = Path("/sys/bus/usb/devices")) -> tuple[list[str], dict | None]:
    if selector is None:
        return [], None
    match = re.fullmatch(r"([0-9]{1,3}):([0-9]{1,3})", selector)
    if not match:
        raise RuntimeError("--usb-camera must identify a Linux USB bus and address, for example 1:2")
    bus, address = map(int, match.groups())
    if not 1 <= bus <= 255 or not 1 <= address <= 127:
        raise RuntimeError("USB camera bus/address is outside the USB device range")
    device = usb_root / f"{bus:03}/{address:03}"
    if not device.exists():
        raise RuntimeError(f"USB device {device} is unavailable; attach the camera to WSL using usbipd first")
    if not os.access(device, os.R_OK | os.W_OK):
        raise RuntimeError(f"QEMU needs read/write access to the selected USB camera {device}")
    camera = False
    for entry in sysfs_root.glob("*"):
        try:
            if int((entry / "busnum").read_text()) == bus and int((entry / "devnum").read_text()) == address:
                camera = any((interface / "bInterfaceClass").read_text().strip().lower() == "0e"
                    for interface in entry.glob(entry.name + ":*"))
                break
        except (OSError, ValueError):
            continue
    if not camera:
        raise RuntimeError("Selected USB device has no USB video interface; select the webcam, not another device")
    return ["-device", f"usb-host,bus=xhci.0,hostbus={bus},hostaddr={address}"], {
        "bus": bus, "address": address, "device": str(device), "transport": "usb-host",
    }
