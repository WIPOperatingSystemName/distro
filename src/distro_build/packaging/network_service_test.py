"""Private target WPA service IPC qualification with no configured interfaces."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import selectors
import subprocess
import tempfile
import time
from types import SimpleNamespace

from ..graph import plan
from ..model import catalog
from ..runtime_native import RuntimeBuild
from .toolkit import PacmanToolkit


def qualify_wpa(project: Path, directory: Path) -> dict:
    project, directory = Path(project).resolve(), Path(directory).resolve()
    if not directory.is_relative_to(project / "out/network-tests") or directory.exists():
        raise ValueError("Use a fresh qualification directory under out/network-tests")
    directory.mkdir(parents=True, mode=0o700)
    packages, identities = [], {}
    for recipe in plan(catalog(project), ["wpa-supplicant"]):
        state = json.loads((project / "out/state/packages" / f"{recipe.name}.json").read_text())
        archive = Path(state["path"])
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != state["sha256"]:
            raise ValueError(f"Target WPA candidate changed: {archive}")
        packages.append(archive)
        identities[recipe.name] = {"path": str(archive), "sha256": actual}
    root = directory / "root"
    PacmanToolkit(project / "out/native-toolkit/prefix").install(root, packages, bootstrap=True,
        expected_hashes={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in packages})
    empty_config = directory / "openssl.cnf"
    empty_config.write_text("")
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C",
                   "OPENSSL_CONF": str(empty_config), "OPENSSL_MODULES": str(root / "usr/lib/ossl-modules")}
    context = SimpleNamespace(stage=root, sysroot=root, work=directory, env=environment)
    loader = lambda path: RuntimeBuild.closed_loader(context, path)
    with tempfile.TemporaryDirectory(prefix="custom-wpa-", dir="/tmp") as temporary:
        socket = Path(temporary) / "bus"
        config = directory / "private-bus.conf"
        config.write_text(f"""<busconfig>
<type>system</type><listen>unix:path={socket}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy>
</busconfig>
""")
        daemon = subprocess.Popen([*loader(root / "usr/bin/dbus-daemon"), f"--config-file={config}",
                                   "--nofork", "--print-address"], env=environment,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        service = None
        try:
            selector = selectors.DefaultSelector()
            selector.register(daemon.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10):
                raise RuntimeError("Private target WPA bus did not become ready")
            address = daemon.stdout.readline().strip()
            if not address.startswith(f"unix:path={socket}"):
                raise RuntimeError("Unexpected private WPA test bus address")
            environment["DBUS_SYSTEM_BUS_ADDRESS"] = address
            environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={temporary}/absent-session-bus"
            with (directory / "wpa-service.log").open("w") as log:
                # -u exports the root D-Bus API. No -i/-N means no network device
                # is attached, scanned, authenticated, or configured by this test.
                service = subprocess.Popen([*loader(root / "usr/bin/wpa_supplicant"), "-u"],
                                           env=environment, stdout=log, stderr=log)
                command = [*loader(root / "usr/bin/dbus-send"), "--system", "--type=method_call",
                           "--print-reply", "--dest=fi.w1.wpa_supplicant1", "/fi/w1/wpa_supplicant1",
                           "org.freedesktop.DBus.Properties.Get", "string:fi.w1.wpa_supplicant1", "string:Interfaces"]
                deadline = time.monotonic() + 10
                while True:
                    response = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=3)
                    if response.returncode == 0:
                        break
                    if service.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"Target WPA D-Bus interface did not become ready: {response.stderr}")
                    time.sleep(0.05)
                if "array [" not in response.stdout or "object path" in response.stdout:
                    raise RuntimeError("WPA fixture unexpectedly configured a physical interface")
                (directory / "wpa-dbus-response.log").write_text(response.stdout)
        finally:
            for process in (service, daemon):
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    result = {"schema": 1, "target_daemon_and_client": True, "authenticated_dbus_ipc": True,
              "network_interfaces_configured": False, "physical_wifi_qualified": False,
              "host_network_changed": False, "host_services_contacted": False,
              "artifacts": identities, "success_marker": "CUSTOM_WPA_DBUS_OK"}
    (directory / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    print("CUSTOM_WPA_DBUS_OK", flush=True)
    return result
