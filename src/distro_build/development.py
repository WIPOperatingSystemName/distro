"""Plan and deploy registered application updates to an existing development VM."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import tomllib

from . import apps, upstream
from .deploy import install, vm_directory
from .guest_agent import GuestAgent
from .source_projects import catalog as source_catalog


def content_identity(paths: list[Path]) -> str:
    """Hash bytes, modes and membership; Git references and timestamps are excluded."""
    digest = hashlib.sha256()

    def visit(path: Path, label: str):
        if path.is_symlink():
            value = [label, "symlink", os.readlink(path)]
        elif path.is_file():
            value = [label, path.stat().st_mode & 0o777, apps.sha256(path)]
        elif path.is_dir():
            value = [label, "directory"]
        else:
            value = [label, "missing"]
        digest.update(json.dumps(value, separators=(",", ":")).encode())
        if path.is_dir() and not path.is_symlink():
            for child in sorted(path.iterdir()):
                if child.name not in apps.EXCLUDED and child.suffix != ".pyc":
                    visit(child, label + "/" + child.name)

    for index, path in enumerate(paths):
        visit(path, str(index) + "/" + path.name)
    return digest.hexdigest()


def base_inputs(project: Path, source_root: Path) -> str:
    paths = [project / name for name in ("bootstrap", "system", "tools/compose-desktop-sdk.py")]
    paths += [project / "src/distro_build" / name for name in
              ("compose.py", "boot.py", "image.py", "runner.py", "model.py", "sources.py", "packaging")]
    paths += [directory for directory in sorted((project / "packages").iterdir())
              if directory.is_dir() and not (directory / "application.toml").exists()]
    for name, record in source_catalog(project).items():
        if record.get("update", "package") == "base":
            paths += [source_root / name / relative for relative in record["inputs"]]
    return content_identity(paths)


def compatibility(guest: dict, installed: dict, sdk: dict, required: set[str]) -> list[str]:
    errors = []
    if not guest.get("capabilities", {}).get("development_agent"):
        errors.append("VM has no development-agent capability")
    originals = {item["name"]: item for item in guest.get("packages", [])}
    for package in sdk["packages"]:
        name = package["package"]
        if name in originals:
            original = originals[name]
            if package["sha256"] != original["sha256"]:
                errors.append(f"SDK/base package differs: {name}")
            versions = original["version"]
            version = versions[0] if isinstance(versions, list) else versions
            if installed.get(name) != version:
                errors.append(f"Installed base package differs: {name}")
    for name in sorted(required):
        original = originals.get(name)
        if original is None:
            errors.append(f"Base package absent from image inventory: {name}")
        else:
            versions = original["version"]
            version = versions[0] if isinstance(versions, list) else versions
            if installed.get(name) != version:
                errors.append(f"Installed native runtime differs: {name}")
    if not ({package["package"] for package in sdk["packages"]} & originals.keys()):
        errors.append("SDK and VM have no common recorded packages")
    if not originals:
        errors.append("VM has no recorded base-package inventory")
    return errors


def restart_policy(applications: dict, names: list[str]) -> dict:
    policies = {name: applications[name].get("development", {"restart": "reopen"}) for name in names}
    return {"desktop": any(policy.get("restart") == "desktop" for policy in policies.values()),
            "services": sorted({unit for policy in policies.values() for unit in policy.get("services", [])}),
            "reopen": [name for name, policy in policies.items() if policy.get("restart", "reopen") == "reopen"]}


def develop(project: Path, *, name: str, source_root: Path | None = None, jobs: int = 4,
            profile: str = "dev", offline: bool = False, plan_only: bool = False,
            adopt_base: bool = False, restart: bool = True, timeout: int = 120, emit=print) -> dict:
    project = project.resolve()
    source_root = (source_root or project / "sources").resolve()
    applications = apps.catalog(project)
    directory = vm_directory(project, name)
    report = {"schema": 1, "vm": name, "success": False, "stage": "preflight", "plan_only": plan_only}
    started = time.monotonic()
    with (directory / "deploy.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if not plan_only:
                report["sources"] = upstream.synchronize(project, source_root, offline=offline, emit=emit)
                report["upstream"] = upstream.check(project, source_root, set(), offline=offline)
                if not offline and any(item["behind"] for item in report["upstream"]):
                    emit({"event": "upstream-integration-required", "repositories": report["upstream"]})
                    raise RuntimeError("Upstream integration required; preserve local work, integrate the reported commits and retry")
            else:
                report["upstream"] = "not checked by read-only plan; required before deployment"
            pointer = json.loads((project / "out/sdk/current.json").read_text())
            sdk = json.loads(Path(pointer["report"]).read_text())
            sysroot = Path(pointer["sysroot"])
            if sdk["identity"] != pointer["identity"] or sdk["sysroot"] != pointer["sysroot"]:
                raise RuntimeError("SDK pointer differs from its recorded report")
            state_path = directory / "development.json"
            baseline = json.loads(state_path.read_text()) if state_path.exists() else None
            report["base_image_sha256"] = json.loads((directory / "state.json").read_text())["base_image_sha256"]
            with GuestAgent(directory / "agent.sock", timeout=timeout) as agent:
                guest = json.loads(agent.execute("/bin/cat", ["/etc/custom-distro/build-manifest.json"])["out-data"])
                installed = dict(line.split(maxsplit=1) for line in agent.execute("/usr/bin/pacman", ["-Q"])["out-data"].splitlines())
                profile_name = guest["profile"].removeprefix("experimental-")
                if profile_name not in {path.stem for path in (project / "profiles").glob("*.toml")}:
                    raise RuntimeError("VM profile is not registered")
                settings = tomllib.loads((project / "profiles" / (profile_name + ".toml")).read_text())
                from .compose import runtime_packages
                selected_packages = settings["system_shell"]["packages"] if guest.get("system_shell") else settings["packages"]
                closure = runtime_packages(project, selected_packages, providers={"libudev": "systemd"})
                names = sorted(set(closure) & applications.keys())
                native = set(closure) - applications.keys()
                errors = compatibility(guest, installed, sdk, native)
                errors += [f"Base runtime package missing: {package}" for package in sorted(native - installed.keys())]
                current_base = base_inputs(project, source_root)
                native_profile = {**settings, "packages": sorted(native)}
                base_context = {"inputs": current_base, "sdk": pointer["identity"], "profile": native_profile,
                                "base_image_sha256": report["base_image_sha256"]}
                if baseline and baseline["base"] != base_context:
                    errors.append("Base inputs, SDK or native profile changed since adoption")
                if not baseline and not adopt_base:
                    errors.append("VM needs a verified development baseline; inspect this plan and use --adopt-base once")
                report["base_errors"] = errors
                report["base"] = base_context
                if errors and not plan_only:
                    raise RuntimeError("Development VM needs base setup: " + "; ".join(errors))
                with (project / "out/state/run.lock").open("a") as build_lock:
                    fcntl.flock(build_lock.fileno(), fcntl.LOCK_EX)
                    manifest = apps.prepare(project, source_root=source_root)
                    report["source_identity"] = manifest["identity"]
                    report["source_root"] = str(source_root)
                    candidates = [apps.build_app(project, package, sysroot, jobs=jobs, offline=offline,
                                                profile=profile, development=True, plan_only=True) for package in names]
                    selected = [item for item in candidates if installed.get(item["name"]) != item["version"]]
                    report["packages"] = [{**item, "action": "deploy" if item in selected else "installed"} for item in candidates]
                    policy = restart_policy(applications, [item["name"] for item in selected])
                    report["restart"] = policy
                    emit({"event": "development-plan", **report})
                    if plan_only:
                        report.update(success=not errors, stage="plan")
                        return report
                    report["stage"] = "build"
                    artifacts = [apps.build_app(project, item["name"], sysroot, jobs=jobs, offline=offline,
                                                profile=profile, development=True) for item in selected]
                # Guard the live inputs against edits while the frozen packages compiled.
                if base_inputs(project, source_root) != current_base:
                    raise RuntimeError("Base inputs changed during compilation; retry before installing")
                report["stage"] = "install"
                if artifacts:
                    report["installation"] = install(agent, artifacts, restart=restart and policy["desktop"],
                                                     **({"system_shell": True} if guest.get("system_shell") else {}))
                    if restart and not policy["desktop"] and policy["services"]:
                        agent.execute("/usr/bin/systemctl", ["--user", "--machine=custom@.host", "try-restart", *policy["services"]])
                else:
                    report["installation"] = {"packages": [], "application_action": "already current"}
                state = {"schema": 1, "base": base_context, "source_identity": manifest["identity"],
                         "packages": candidates, "qualification": "installed versions verified; exercise updated applications"}
                temporary = state_path.with_suffix(".next")
                temporary.write_text(json.dumps(state, indent=2) + "\n")
                temporary.replace(state_path)
                report.update(success=True, stage="complete")
                return report
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            (directory / "development-report.json").write_text(json.dumps(report, indent=2) + "\n")
