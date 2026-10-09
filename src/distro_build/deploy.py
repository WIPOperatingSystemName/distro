"""Build selected applications and install them into a running development VM."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from . import apps
from .boot import sha256
from .guest_agent import GuestAgent
from .packaging import inspect_package


def vm_directory(project: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}", name):
        raise RuntimeError("Invalid VM name")
    parent = project / "out/vms"
    directory = parent / name
    if parent.is_symlink() or directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("Named VM does not exist; boot desktop-dev with --name first")
    info = directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("Development VM directory must be private and owned by the current user")
    for filename in ("state.json", "vm.lock", "deploy.lock", "agent.sock", "deployment.json"):
        if (directory / filename).is_symlink():
            raise RuntimeError("Development VM control files cannot be symlinks")
    state = json.loads((directory / "state.json").read_text())
    if state.get("schema") != 1 or state.get("disk") != str(directory / "disk.img"):
        raise RuntimeError("Invalid saved VM identity")
    if not (directory / "agent.sock").is_socket():
        raise RuntimeError("No development agent socket; boot the desktop-dev image with this --name")
    with (directory / "vm.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return directory
    raise RuntimeError("The named VM is not running")


def install(agent: GuestAgent, artifacts: list[dict], *, restart: bool = True) -> dict:
    """One ALPM transaction for all selected packages, with transfer verification."""
    selected = []
    for artifact in artifacts:
        path = Path(artifact["path"])
        if sha256(path) != artifact["sha256"]:
            raise RuntimeError("Deployment package differs from its build receipt")
        package = inspect_package(path)["metadata"]
        if package["pkgname"] != [artifact["package"]]:
            raise RuntimeError("Deployment package name differs from its build receipt")
        selected.append({"name": artifact["package"], "version": package["pkgver"][0],
                         "sha256": artifact["sha256"], "path": str(path)})
    identifier = secrets.token_hex(12)
    destination = "/var/lib/custom-distro/deploy/" + identifier
    agent.execute("/bin/mkdir", ["-p", destination])
    paths = []
    for index, package in enumerate(selected):
        path = destination + f"/{index}.pkg.tar.xz"
        agent.transfer(Path(package["path"]), path)
        actual = agent.execute("/bin/busybox", ["sha256sum", path])["out-data"].split()
        if not actual or actual[0] != package["sha256"]:
            raise RuntimeError("Transferred guest package checksum mismatch")
        paths.append(path)
    transaction = agent.execute("/usr/bin/pacman", ["-U", "--noconfirm", *paths])
    for package in selected:
        query = agent.execute("/usr/bin/pacman", ["-Q", package["name"]])["out-data"].strip()
        if query != f"{package['name']} {package['version']}":
            raise RuntimeError(f"Guest package version verification failed: {package['name']}")
    restarted = False
    # Restart the PAM session as a unit so all compositor clients release their
    # old executable mappings. --no-restart lets users keep unsaved work open.
    if restart and selected:
        agent.execute("/bin/rm", ["-f", "/run/user/1000/telorgon-wayland-socket"])
        agent.execute("/usr/bin/systemctl", ["restart", "custom-distro-desktop.service"])
        agent.execute("/usr/bin/systemctl", ["is-active", "custom-distro-desktop.service"])
        readiness = """i=0
while [ "$i" -lt 45 ]; do
    if [ -s /run/user/1000/telorgon-wayland-socket ] &&
       read -r socket < /run/user/1000/telorgon-wayland-socket && [ -S "$socket" ]; then
        case "$socket" in /run/user/1000/wayland-*) exit 0;; esac
    fi
    i=$((i+1))
    sleep 1
done
echo 'The restarted compositor did not publish a ready Wayland socket.' >&2
exit 1
"""
        agent.execute("/bin/sh", ["-ec", readiness])
        restarted = True
    agent.execute("/bin/rm", ["-rf", destination])
    return {"packages": selected, "desktop_restarted": restarted,
            "application_action": "reopen updated applications" if not restarted else "desktop session restarted",
            "transaction_output": transaction["out-data"],
            "qualification": "guest installation and package versions verified; GUI behavior requires testing"}


def deploy(project: Path, names: list[str], *, name: str, source_root: Path | None = None,
           jobs: int = 4, offline: bool = True, profile: str = "dev", restart: bool = True,
           timeout: int = 120, emit=print) -> dict:
    project = project.resolve()
    selected = list(dict.fromkeys(names))
    if not selected or set(selected) - apps.catalog(project).keys():
        raise RuntimeError("Deploy requires known Telorgon application package names")
    directory = vm_directory(project, name)
    started = time.monotonic()
    with (directory / "deploy.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        report = {"schema": 1, "vm": name,
                  "base_image_sha256": json.loads((directory / "state.json").read_text())["base_image_sha256"],
                  "success": False, "stage": "preflight"}
        record = directory / "deployment.json"
        record.write_text(json.dumps(report, indent=2) + "\n")
        try:
            # Fail before compiling if this disk predates the development agent.
            with GuestAgent(directory / "agent.sock", timeout=timeout) as agent:
                agent.call("guest-ping")
                info = agent.call("guest-info")
                supported = {command["name"] for command in info["supported_commands"] if command["enabled"]}
                required = {"guest-file-open", "guest-file-write", "guest-file-close", "guest-file-flush", "guest-exec", "guest-exec-status"}
                if not required <= supported:
                    raise RuntimeError("Guest agent does not enable the required deployment commands")
                guest = json.loads(agent.execute("/bin/cat", ["/etc/custom-distro/build-manifest.json"])["out-data"])
                if not guest.get("capabilities", {}).get("development_agent"):
                    raise RuntimeError("Guest image does not identify itself as a development image")
            sdk = project / "out/sdk/current.json"
            if not sdk.is_file():
                raise RuntimeError("Desktop SDK is missing; run desktop-dev once before deploying")
            sysroot = Path(json.loads(sdk.read_text())["sysroot"])
            report["stage"] = "build"
            with (project / "out/state/run.lock").open("a") as build_lock:
                fcntl.flock(build_lock.fileno(), fcntl.LOCK_EX)
                manifest = apps.prepare(project, source_root=source_root)
                artifacts = []
                for package in selected:
                    emit({"event": "deploy-build", "package": package, "profile": profile})
                    artifacts.append(apps.build_app(project, package, sysroot, jobs=jobs, offline=offline,
                        profile=profile, development=True))
            vm_directory(project, name)
            report["source_identity"] = manifest["identity"]
            report["stage"] = "install"
            emit({"event": "deploy-install", "vm": name})
            with GuestAgent(directory / "agent.sock", timeout=timeout) as agent:
                report.update(install(agent, artifacts, restart=restart))
            report["success"] = True
            report["stage"] = "complete"
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            record.write_text(json.dumps(report, indent=2) + "\n")
        return report
