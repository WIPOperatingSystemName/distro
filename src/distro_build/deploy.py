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
    for filename in ("state.json", "vm.lock", "deploy.lock", "agent.sock", "deployment.json", "development.json", "development.next", "development-report.json"):
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


def install(agent: GuestAgent, artifacts: list[dict], *, restart: bool = True, system_shell: bool = False) -> dict:
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
    # Restart the selected compositor service so clients release old executable
    # mappings. --no-restart lets users keep unsaved work open.
    if restart and selected:
        service = "telorgon-shell.service" if system_shell else "custom-distro-desktop.service"
        if not system_shell:
            agent.execute("/bin/rm", ["-f", "/run/user/1000/telorgon-wayland-socket"])
        agent.execute("/usr/bin/systemctl", ["restart", service])
        agent.execute("/usr/bin/systemctl", ["is-active", service])
        readiness = """i=0
while [ "$i" -lt 45 ]; do
    [ ! -S /run/telorgon-shell/wayland-system ] || exit 0
    i=$((i+1))
    sleep 1
done
echo 'The system compositor did not create its Wayland socket.' >&2
exit 1
""" if system_shell else """i=0
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
            "application_action": "reopen updated applications" if not restarted else "system shell restarted" if system_shell else "desktop session restarted",
            "transaction_output": transaction["out-data"],
            "qualification": "guest installation and package versions verified; GUI behavior requires testing"}


def deploy(project: Path, names: list[str], *, name: str, source_root: Path | None = None,
           jobs: int = 4, offline: bool = True, profile: str = "dev", restart: bool = True,
           timeout: int = 120, reuse_built: bool = False, emit=print) -> dict:
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
            if reuse_built:
                # Use a private manifest while an image build owns the common
                # preparation lock. Cached packages remain immutable inputs.
                manifest, artifacts = reuse_packages(project, selected, sysroot, profile=profile,
                    source_root=source_root, output=directory / "deploy-sources")
                report["reused_built_packages"] = True
            else:
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
                report.update(install(agent, artifacts, restart=restart,
                                      **({"system_shell": True} if guest.get("system_shell") else {})))
            report["success"] = True
            report["stage"] = "complete"
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            record.write_text(json.dumps(report, indent=2) + "\n")
        return report


def reuse_packages(project: Path, selected: list[str], sysroot: Path, *, profile: str,
                   source_root: Path | None, output: Path) -> tuple[dict, list[dict]]:
    """Require current sources, recipe, SDK and profile to match completed builds."""
    manifest = apps.prepare(project, output, source_root=source_root)
    artifacts = []
    for package in selected:
        plan = apps.build_app(project, package, sysroot, manifest=Path(manifest["manifest"]),
                              profile=profile, development=False, plan_only=True)
        if not plan["cached"]:
            raise RuntimeError(f"No matching completed build for {package}; build it with build.py apps build first")
        receipt = project / "out/apps/build" / package / plan["build_identity"] / "result.json"
        artifact = json.loads(receipt.read_text())
        if (artifact.get("package") != package or artifact.get("build_identity") != plan["build_identity"]
                or sha256(Path(artifact["path"])) != artifact.get("sha256")):
            raise RuntimeError(f"Completed build receipt changed for {package}")
        artifacts.append(artifact)
    return manifest, artifacts
