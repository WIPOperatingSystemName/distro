"""Independent target D-Bus and PipeWire, with explicit runtime qualification.

Build tools run on the declared host seed. Shared libraries and executables are
cross compiled with the source-built compiler and private dependency sysroot.
The programs are exercised through that sysroot's loader; --list must resolve
every library within the supplied source-built roots before execution.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import socket
import sys
import tempfile
import time

from .desktop_native import DesktopBuild


class RuntimeBuild(DesktopBuild):
    def closed_loader(self, binary: Path) -> list[str]:
        loader = self.sysroot / "usr/lib/ld-linux-x86-64.so.2"
        command = [str(loader), "--inhibit-cache", "--library-path",
                   f"{self.stage}/usr/lib:{self.sysroot}/usr/lib"]
        report = subprocess.run([*command, "--list", str(binary)], env=self.env,
                                capture_output=True, text=True, check=True)
        for line in report.stdout.splitlines():
            if "linux-vdso" in line:
                continue
            matched = re.search(r"(?:=>\s*)?(/[^\s]+)\s+\(0x[0-9a-f]+\)", line)
            if not matched:
                raise RuntimeError(f"Unrecognized loader resolution: {line}")
            resolved = Path(matched.group(1)).resolve()
            if not any(resolved.is_relative_to(root.resolve()) for root in (self.stage, self.sysroot)):
                raise RuntimeError(f"Target runtime resolves outside its package roots: {resolved}")
        (self.work / f"{binary.name}-loader-list.txt").write_text(report.stdout)
        return [*command, str(binary)]

    def closed_probe(self, binary: Path, *arguments: str) -> str:
        result = subprocess.run([*self.closed_loader(binary), *arguments], env=self.env,
                                text=True, capture_output=True, check=True)
        print(result.stdout, end="", flush=True)
        return result.stdout

    def dbus_probe(self) -> None:
        """Exercise a private Unix bus and real client authentication/message IPC."""
        binary = self.stage / "usr/bin/dbus-daemon"
        daemon = self.closed_loader(binary)
        client = self.closed_loader(self.stage / "usr/bin/dbus-send")
        # Linux Unix socket paths are limited to 108 bytes. Content-addressed
        # work directories exceed that, so use a private short-lived /tmp path.
        temporary = tempfile.TemporaryDirectory(prefix="custom-dbus-", dir="/tmp")
        socket = Path(temporary.name) / "bus.sock"
        configuration = self.work / "probe-bus.conf"
        configuration.write_text(f"""<busconfig>
  <type>session</type>
  <listen>unix:path={socket}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
    <allow own="*"/>
  </policy>
</busconfig>
""")
        process = subprocess.Popen([*daemon, f"--config-file={configuration}", "--nofork", "--print-address"],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # A ready bus prints its address before accepting client calls.
            import selectors
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10):
                raise RuntimeError("Private target D-Bus daemon did not become ready")
            address = process.stdout.readline().strip()
            if not address.startswith(f"unix:path={socket}"):
                details = process.stderr.read() if process.poll() is not None else ""
                raise RuntimeError(f"Unexpected private D-Bus address: {address}; {details}")
            env = dict(self.env, DBUS_SESSION_BUS_ADDRESS=address)
            response = subprocess.run([*client, "--session", "--dest=org.freedesktop.DBus",
                                       "--type=method_call", "--print-reply", "/org/freedesktop/DBus",
                                       "org.freedesktop.DBus.ListNames"], env=env,
                                      text=True, capture_output=True, check=True, timeout=10)
            if 'string "org.freedesktop.DBus"' not in response.stdout:
                raise RuntimeError("Target D-Bus client did not receive the daemon's name list")
            print("CUSTOM_DBUS_IPC_OK", flush=True)
            (self.work / "dbus-runtime-probe.json").write_text(json.dumps(
                {"authenticated_ipc": True, "daemon": str(binary),
                 "response": response.stdout, "scope": "private host Unix socket via target loader"}, indent=2) + "\n")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            temporary.cleanup()

    def dbus_socket_activation_probe(self) -> None:
        """Exercise the real LISTEN_FDS API with an inherited private listener."""
        daemon = self.closed_loader(self.stage / "usr/bin/dbus-daemon")
        client = self.closed_loader(self.stage / "usr/bin/dbus-send")
        configuration = self.work / "probe-socket-activation.conf"
        configuration.write_text("<busconfig><type>session</type><listen>systemd:</listen><auth>EXTERNAL</auth>"
            '<policy context="default"><allow send_destination="*"/>'
            '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>\n')
        launcher = self.work / "socket-activation-launcher.py"
        launcher.write_text("import os,sys\nfd=int(sys.argv[1])\n"
            "if fd!=3: os.dup2(fd,3);os.close(fd)\n"
            "os.set_inheritable(3,True)\nenv=dict(os.environ)\n"
            "env.update(LISTEN_PID=str(os.getpid()),LISTEN_FDS='1',LISTEN_FDNAMES='dbus')\n"
            "os.execve(sys.argv[2],sys.argv[2:],env)\n")
        with tempfile.TemporaryDirectory(prefix="custom-dbus-fd-", dir="/tmp") as directory:
            address = "unix:path=" + str(Path(directory) / "bus.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(Path(directory) / "bus.sock"))
                listener.listen(8)
                process = subprocess.Popen([sys.executable, str(launcher), str(listener.fileno()),
                    *daemon, f"--config-file={configuration}", "--address=systemd:", "--nofork",
                    "--nopidfile", "--systemd-activation", "--nosyslog", "--print-address"],
                    pass_fds=(listener.fileno(),), env=self.env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                try:
                    import selectors
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        if not selector.select(timeout=10):
                            raise RuntimeError("Target D-Bus did not accept its inherited listener")
                    published = process.stdout.readline().strip()
                    if not published.startswith(address + ",guid="):
                        details = process.stderr.read() if process.poll() is not None else ""
                        raise RuntimeError(f"Unexpected socket-activated D-Bus address: {published!r}; {details}")
                    response = subprocess.run([*client, "--session", "--dest=org.freedesktop.DBus",
                        "--type=method_call", "--print-reply", "/org/freedesktop/DBus",
                        "org.freedesktop.DBus.ListNames"],
                        env=dict(self.env, DBUS_SESSION_BUS_ADDRESS=address),
                        capture_output=True, text=True, check=True, timeout=10)
                    if 'string "org.freedesktop.DBus"' not in response.stdout:
                        raise RuntimeError("Socket-activated target D-Bus did not reply to its actual client")
                    (self.work / "dbus-socket-activation-probe.json").write_text(json.dumps({
                        "inherited_listener": True, "target_libsystemd_api": True,
                        "authenticated_ipc": True, "response": response.stdout,
                        "scope": "private host listener via target sd_listen_fds and explicit loader",
                        "pid1_manager_registration_qualified_by_this_probe": False}, indent=2) + "\n")
                    print("CUSTOM_DBUS_SOCKET_ACTIVATION_OK", flush=True)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def pipewire_probe(self) -> None:
        """Start the real server and query its core through the native protocol."""
        server = self.closed_loader(self.stage / "usr/bin/pipewire")
        client = self.closed_loader(self.stage / "usr/bin/pw-cli")
        with tempfile.TemporaryDirectory(prefix="custom-pipewire-", dir="/tmp") as directory:
            runtime = Path(directory)
            runtime.chmod(0o700)
            env = dict(self.env, XDG_RUNTIME_DIR=directory, PIPEWIRE_RUNTIME_DIR=directory,
                       PIPEWIRE_REMOTE="pipewire-0", PIPEWIRE_DEBUG="1",
                       DBUS_SESSION_BUS_ADDRESS=f"unix:path={runtime}/absent-session-bus",
                       DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={runtime}/absent-system-bus",
                       DISABLE_RTKIT="1",
                       PIPEWIRE_CONFIG_DIR=str(self.stage / "usr/share/pipewire"),
                       PIPEWIRE_MODULE_DIR=str(self.stage / "usr/lib/pipewire-0.3"),
                       SPA_PLUGIN_DIR=str(self.stage / "usr/lib/spa-0.2"))
            log_path = self.work / "pipewire-runtime-probe.log"
            with log_path.open("w") as log:
                process = subprocess.Popen(server, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 10
                    while not (runtime / "pipewire-0").exists():
                        if process.poll() is not None:
                            raise RuntimeError(f"Target PipeWire server exited; see {log_path}")
                        if time.monotonic() > deadline:
                            raise RuntimeError(f"Target PipeWire server did not become ready; see {log_path}")
                        time.sleep(0.05)
                    response = subprocess.run([*client, "-r", "pipewire-0", "info", "0"], env=env,
                                              text=True, capture_output=True, check=True, timeout=10)
                    if "PipeWire:Interface:Core" not in response.stdout:
                        raise RuntimeError(f"Target PipeWire client did not receive core information: {response.stdout}")
                    print("CUSTOM_PIPEWIRE_IPC_OK", flush=True)
                    (self.work / "pipewire-runtime-probe.json").write_text(json.dumps(
                        {"native_protocol_ipc": True, "response": response.stdout,
                         "scope": "private host Unix socket via target loader",
                         "physical_audio": False, "wireplumber": False}, indent=2) + "\n")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def build_runtime(name: str) -> None:
    build = RuntimeBuild()
    if name == "dbus":
        build.meson(["-Dapparmor=disabled", "-Dselinux=disabled", "-Dlibaudit=disabled",
                     "-Dsystemd=enabled", "-Dx11_autolaunch=disabled", "-Dlaunchd=disabled",
                     "-Dsystemd_system_unitdir=/usr/lib/systemd/system",
                     "-Dsystemd_user_unitdir=/usr/lib/systemd/user",
                     "-Dmodular_tests=disabled", "-Dinstalled_tests=false", "-Dintrusive_tests=false",
                     "-Ddoxygen_docs=disabled", "-Dducktype_docs=disabled", "-Dxml_docs=disabled",
                     "-Dqt_help=disabled", "-Duser_session=false", "-Druntime_dir=/run",
                     "-Ddbus_daemondir=/usr/bin", "-Dsession_socket_dir=/tmp",
                     "-Dsystem_socket=/run/dbus/system_bus_socket", "-Dsystem_pid_file=/run/dbus/pid"])
        build.license(name, ["COPYING", "AUTHORS"])
        build.closed_probe(build.stage / "usr/bin/dbus-daemon", "--version")
        build.dbus_probe()
        build.dbus_socket_activation_probe()
    elif name == "pipewire":
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
                    "alsa", "bluez5", "jack", "v4l2", "libcamera", "vulkan", "pw-cat", "sdl2",
                    "sndfile", "libmysofa", "libpulse", "roc", "avahi", "echo-cancel-webrtc", "libusb",
                    "raop", "lv2", "x11", "x11-xfixes", "libcanberra", "avb", "flatpak", "readline",
                    "gsettings", "compress-offload", "opus", "libffado", "gsettings-pulse-schema",
                    "snap", "ebur128", "fftw", "onnxruntime")
        build.meson([*[f"-D{option}=disabled" for option in disabled],
                     "-Ddbus=enabled", "-Dudev=enabled", "-Dsession-managers=[]",
                     "-Drlimits-install=false", "-Dlegacy-rtkit=false"])
        build.license(name, ["COPYING", "LICENSE"])
        build.closed_probe(build.stage / "usr/bin/pipewire", "--version")
        build.closed_probe(build.stage / "usr/bin/pw-cli", "--version")
        build.pipewire_probe()
        required = ["usr/lib/libpipewire-0.3.so", "usr/lib/spa-0.2/support/libspa-support.so",
                    "usr/lib/pipewire-0.3/libpipewire-module-client-node.so"]
        for path in required:
            if not (build.stage / path).is_file():
                raise RuntimeError(f"Required Telorgon PipeWire runtime component missing: {path}")
        description = build.stage / "usr/share/doc/pipewire/qualification.txt"
        description.parent.mkdir(parents=True, exist_ok=True)
        description.write_text("PipeWire server, client API, SPA audio/video conversion and software sources.\n"
                               "No physical ALSA, Bluetooth, JACK, camera backends or WirePlumber yet.\n"
                               "The upstream PulseAudio protocol module is built but not runtime qualified.\n"
                               "Hardware audio and multimedia policy require additional source-built packages.\n")
    else:
        raise RuntimeError(f"Unknown target runtime: {name}")
    for path in build.stage.rglob("*.la"):
        path.unlink()
    build.audit()
