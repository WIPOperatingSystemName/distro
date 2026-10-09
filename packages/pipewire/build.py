#!/usr/bin/env python3
"""Build PipeWire with guest ALSA audio and V4L2 camera support."""
from pathlib import Path
import hashlib
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.runtime_native import RuntimeBuild

name = "pipewire"
build = RuntimeBuild()
# Upstream embeds /usr/lib/pipewire-0.3 in every module's RUNPATH.
# All module dependencies in this profile live in the standard /usr/lib
# directory; remove that optional search path before compilation. Keep
# the patch exact and recorded so a source update requires review.
modules = build.source / "src/modules/meson.build"
original = modules.read_bytes()
expected = "e26e8ac28d1909402ba41e48495d6e75980aed7f76d6f706ebd915c5ec104ab9"
if hashlib.sha256(original).hexdigest() != expected:
    raise RuntimeError("Pinned PipeWire module build rules changed; review the RUNPATH patch")
old, new = b"install_rpath: modules_install_dir,", b"install_rpath: '',"
if original.count(old) != 48:
    raise RuntimeError("Unexpected PipeWire module RUNPATH declaration count")
modified = original.replace(old, new)
modules.write_bytes(modified)
(build.work / "source-overlays.json").write_text(json.dumps(
    [{"file": "src/modules/meson.build", "purpose": "no installed module RUNPATH",
      "before_sha256": expected, "after_sha256": hashlib.sha256(modified).hexdigest(),
      "replacements": 48}], indent=2) + "\n")
disabled = ("docs", "man", "examples", "tests", "installed_tests", "gstreamer",
            "gstreamer-device-provider", "libsystemd", "logind", "systemd-system-service",
            "systemd-user-service", "selinux", "pipewire-alsa", "pipewire-jack", "pipewire-v4l2",
            "bluez5", "jack", "libcamera", "vulkan", "pw-cat", "sdl2",
            "sndfile", "libmysofa", "libpulse", "roc", "avahi", "echo-cancel-webrtc", "libusb",
            "raop", "lv2", "x11", "x11-xfixes", "libcanberra", "avb", "flatpak", "readline",
            "gsettings", "compress-offload", "opus", "libffado", "gsettings-pulse-schema",
            "snap", "ebur128", "fftw", "onnxruntime")
build.meson([*[f"-D{option}=disabled" for option in disabled],
             "-Ddbus=enabled", "-Dudev=enabled", "-Dalsa=enabled", "-Dv4l2=enabled", "-Dsession-managers=[]",
             "-Drlimits-install=false", "-Dlegacy-rtkit=false"])
build.license(name, ["COPYING", "LICENSE"])
build.closed_probe(build.stage / "usr/bin/pipewire", "--version")
build.closed_probe(build.stage / "usr/bin/pw-cli", "--version")
build.pipewire_probe()
required = ["usr/lib/libpipewire-0.3.so", "usr/lib/spa-0.2/support/libspa-support.so",
            "usr/lib/pipewire-0.3/libpipewire-module-client-node.so",
            "usr/lib/spa-0.2/alsa/libspa-alsa.so", "usr/lib/spa-0.2/v4l2/libspa-v4l2.so"]
for path in required:
    if not (build.stage / path).is_file():
        raise RuntimeError(f"Required Telorgon PipeWire runtime component missing: {path}")
description = build.stage / "usr/share/doc/pipewire/qualification.txt"
description.parent.mkdir(parents=True, exist_ok=True)
description.write_text("PipeWire server, client API, SPA audio/video conversion and software sources.\n"
                       "ALSA and V4L2 device backends included; guest device testing is recorded separately.\n"
                       "The upstream PulseAudio protocol module is built but not runtime qualified.\n"
                       "WirePlumber supplies session policy. Bluetooth and JACK are not included.\n")
for path in build.stage.rglob("*.la"):
    path.unlink()
build.audit()
