"""Compose the source-built console slice through real package transactions."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tarfile
import tomllib

from .boot import build_initramfs, stage_esp, validate_efi
from .media import build_disk, root_uuid
from .model import BuildError, catalog
from .packaging import PacmanToolkit, export_package, inspect_package
from .sources import sha256


def _artifact(project: Path, name: str) -> dict:
    state = project / "out/state/packages" / f"{name}.json"
    if not state.is_file():
        state = project / "out/state/apps" / f"{name}.json"
    if not state.is_file():
        raise BuildError(f"missing package artifact for {name}; build its catalog recipe first")
    record = json.loads(state.read_text())
    archive = Path(record["path"])
    if not archive.is_file() or sha256(archive) != record["sha256"]:
        raise BuildError(f"{name}: package artifact differs from its recorded build")
    metadata = inspect_package(archive)
    if metadata["name"] != name or record.get("package") != name:
        raise BuildError(f"{name}: package archive identity differs from its build record")
    record["package_metadata"] = metadata["metadata"]
    return record


def _fresh(path: Path, project: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(project / "out"):
        raise BuildError("generated roots must be beneath this project's out directory")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def runtime_packages(project: Path, selected: list[str], *, providers: dict[str, str] | None = None) -> list[str]:
    """Resolve the image's runtime closure, excluding native build tools."""
    recipes = catalog(project)
    from .apps import catalog as application_catalog
    applications = application_catalog(project)
    providers = providers or {}
    ordered, visiting, complete = [], set(), set()

    def visit(name: str) -> None:
        name = providers.get(name, name)
        if name in complete:
            return
        if name in visiting:
            return
        if name not in recipes and name not in applications:
            raise BuildError(f"image runtime package is missing from the catalog: {name}")
        visiting.add(name)
        dependencies = recipes[name].dependencies.get("runtime", ()) if name in recipes else applications[name]["runtime"]["packages"]
        for dependency in dependencies:
            visit(dependency)
        visiting.remove(name)
        complete.add(name)
        ordered.append(name)

    for name in selected:
        visit(name)
    return ordered


def _source_summary(record: dict) -> dict | list:
    sources = record["provenance"]["sources"]
    if not isinstance(sources, dict) or "sources" not in sources:
        return sources
    return {"kind": sources["kind"], "identity": sources["identity"],
            "origin": sources.get("origin"), "overlays": sources["overlays"],
            "projects": {name: {key: item[key] for key in ("identity", "cargo_lock_sha256", "git_head", "git_dirty") if key in item}
                         for name, item in sources["sources"].items()}}


def base_package(project: Path, profile: str = "console") -> Path:
    system = project / "system"
    required = [system / name for name in ("os-release", "init", "runtime-init", "init-command")]
    if any(not path.is_file() for path in required):
        raise BuildError("console system policy is incomplete")
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    digest.update(profile.encode())
    for path in required:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    identity = digest.hexdigest()
    stage = _fresh(project / "out/work/custom-distro-base" / identity / "stage", project)
    files = {
        "etc/os-release": (system / "os-release").read_text(),
        "etc/hostname": "custom-distro\n",
        "etc/passwd": "root:x:0:0:root:/root:/bin/sh\n",
        "etc/group": "root:x:0:\n",
        "etc/shadow": "root:!*:0:0:99999:7:::\n",
        "etc/custom-distro/init-command": (system / "init-command").read_text(),
        "etc/custom-distro/build-manifest.json": '{"state":"generated during image composition"}\n',
        "etc/custom-distro/package-inventory.json": '{"state":"generated during image composition"}\n',
        "usr/lib/custom-distro/init": (system / "runtime-init").read_text(),
        "init": (system / "init").read_text(),
    }
    if profile != "console":
        files.update({
            "etc/passwd": "root:x:0:0:root:/root:/bin/sh\nmessagebus:x:81:81:System D-Bus:/:/bin/false\ncustom:x:1000:1000:Custom Distro user:/home/custom:/bin/sh\nnobody:x:65534:65534:Nobody:/:/bin/false\n",
            "etc/group": "root:x:0:\nmessagebus:x:81:\ncustom:x:1000:\nusers:x:100:\nnogroup:x:65534:\n",
            "etc/shadow": "root:!*:20332:0:99999:7:::\nmessagebus:!*:20332:0:99999:7:::\ncustom:!*:20332:0:99999:7:::\nnobody:!*:20332:0:99999:7:::\n",
            "etc/custom-distro/init-command": "/sbin/init\n",
            "etc/machine-id": "",
        })
    for name, content in files.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755 if name in {"init", "usr/lib/custom-distro/init"} else 0o600 if name == "etc/shadow" else 0o644)
    for name in ("dev", "proc", "sys", "run", "tmp", "root", "var/log", "var/cache", "mnt", *(["home/custom"] if profile != "console" else [])):
        (stage / name).mkdir(parents=True, exist_ok=True)
    licenses = stage / "usr/share/licenses/custom-distro-base"
    licenses.mkdir(parents=True)
    shutil.copy2(project / "LICENSE", licenses / "LICENSE")
    for path in stage.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    (stage / "tmp").chmod(0o1777)
    (stage / "root").chmod(0o700)
    if profile != "console":
        (stage / "home/custom").chmod(0o700)
    metadata = {"name": "custom-distro-base", "version": "0.1.0", "revision": 1,
                "arch": "any", "description": "Custom Distro experimental console system policy",
                "licenses": ["MIT"], "depends": ["busybox"],
                "backup": ["etc/hostname", "etc/passwd", "etc/group", "etc/shadow", "etc/custom-distro/init-command", "etc/custom-distro/build-manifest.json"]}
    metadata["backup"].append("etc/custom-distro/package-inventory.json")
    artifact = export_package(stage, project / "out/packages/custom-distro-base" / identity, metadata,
                              source_date_epoch=1756684800,
                              provenance={"source_kind": "catalog-local", "system_identity": identity,
                                          "scope": "experimental root console, no authenticated desktop login"})
    return artifact.path


def system_policy_package(project: Path, profile: str = "systemd") -> Path:
    source = project / "system/systemd"
    desktop = project / "system/desktop" if profile in {"desktop", "desktop-use", "desktop-dev"} else None
    normal = project / "system/desktop-use" if profile in {"desktop-use", "desktop-dev"} else None
    probe = None
    if desktop and not normal:
        pointer = project / "out/qualification/desktop-session-probe/current.json"
        if not pointer.is_file():
            raise BuildError("desktop qualification client is missing; run tools/build-desktop-session-probe.py")
        probe = json.loads(pointer.read_text())
        if sha256(Path(probe["binary"])) != probe["sha256"]:
            raise BuildError("desktop qualification client differs from its recorded build")
    digest = hashlib.sha256(Path(__file__).read_bytes())
    digest.update(profile.encode())
    for tree in (source, *([desktop] if desktop else []), *([normal] if normal else [])):
        for path in sorted(tree.rglob("*")):
            if path.is_symlink():
                digest.update(path.relative_to(tree).as_posix().encode())
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                digest.update(path.relative_to(tree).as_posix().encode())
                digest.update(path.read_bytes())
    if probe:
        digest.update(probe["sha256"].encode())
        digest.update(sha256(Path(probe["report"])).encode())
    identity = digest.hexdigest()
    stage = _fresh(project / "out/work/custom-distro-session" / identity / "stage", project)
    shutil.copytree(source, stage, dirs_exist_ok=True)
    if desktop:
        shutil.copytree(desktop, stage, dirs_exist_ok=True)
    if normal:
        # Normal sessions contain no qualification units, clients or scripts.
        for path in list(stage.rglob("*")):
            if path.is_file() and (path.name.startswith("check-") or "-check.service" in path.as_posix()):
                path.unlink()
        for path in sorted(stage.rglob("*"), reverse=True):
            if path.is_dir() and "-check.service" in path.as_posix():
                path.rmdir()
        shutil.copytree(normal, stage, dirs_exist_ok=True, symlinks=True)
    if probe:
        binary = stage / "usr/libexec/custom-distro/desktop-session-probe"
        binary.parent.mkdir(parents=True)
        shutil.copy2(probe["binary"], binary)
        receipt = stage / "usr/share/custom-distro/qualification/desktop-probe.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"sha256": probe["sha256"], "build_identity": probe["identity"],
                          "report_sha256": sha256(Path(probe["report"])), "scope": "real Wayland frame, presentation, focus, input and redraw required"}, indent=2) + "\n")
    links = {
        "sbin/init": "../usr/lib/systemd/systemd", "bin/login": "../usr/bin/login",
        "var/lib/dbus/machine-id": "/etc/machine-id",
        "etc/systemd/system/default.target": "/usr/lib/systemd/system/multi-user.target",
        "etc/systemd/system/dbus-org.freedesktop.login1.service": "/usr/lib/systemd/system/systemd-logind.service",
        "etc/systemd/system/multi-user.target.wants/dbus.service": "../dbus.service",
        "etc/systemd/system/sockets.target.wants/dbus.socket": "../dbus.socket",
        "etc/systemd/system/multi-user.target.wants/systemd-logind.service": "/usr/lib/systemd/system/systemd-logind.service",
        "etc/systemd/system/multi-user.target.wants/custom-distro-session-check.service": "../custom-distro-session-check.service",
        "etc/systemd/system/multi-user.target.wants/custom-distro-system-check.service": "../custom-distro-system-check.service",
    }
    if desktop:
        links.pop("etc/systemd/system/multi-user.target.wants/custom-distro-system-check.service")
        links["etc/systemd/system/default.target"] = "/usr/lib/systemd/system/graphical.target"
        links.update({
            "etc/systemd/system/graphical.target.wants/custom-distro-system-check.service": "../custom-distro-system-check.service",
            "etc/systemd/system/multi-user.target.wants/seatd.service": "../seatd.service",
            "etc/systemd/system/multi-user.target.wants/NetworkManager.service": "/usr/lib/systemd/system/NetworkManager.service",
            "etc/systemd/system/multi-user.target.wants/polkit.service": "/usr/lib/systemd/system/polkit.service",
            "etc/systemd/system/multi-user.target.wants/upower.service": "/usr/lib/systemd/system/upower.service",
            "etc/systemd/system/multi-user.target.wants/power-profiles-daemon.service": "/usr/lib/systemd/system/power-profiles-daemon.service",
            "etc/systemd/user/default.target.wants/dbus.service": "../dbus.service",
            "etc/systemd/user/sockets.target.wants/dbus.socket": "../dbus.socket",
        })
    if normal:
        links = {name: target for name, target in links.items() if "-check.service" not in name}
        links["etc/systemd/system/graphical.target.wants/custom-distro-desktop.service"] = "../custom-distro-desktop.service"
    for name, target in links.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() and os.readlink(path) == target:
            continue
        path.symlink_to(target)
    for path in stage.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.is_relative_to(stage / "usr/lib/custom-distro") or path.is_relative_to(stage / "usr/libexec/custom-distro") else 0o644)
    licenses = stage / "usr/share/licenses/custom-distro-session"
    licenses.mkdir(parents=True)
    shutil.copy2(project / "LICENSE", licenses / "LICENSE")
    for directory in stage.rglob("*"):
        if directory.is_dir() and not directory.is_symlink():
            directory.chmod(0o755)
    (licenses / "LICENSE").chmod(0o644)
    metadata = {"name": "custom-distro-session", "version": "0.1.0", "revision": 3 if normal else (2 if desktop else 1),
                "arch": "x86_64" if probe else "any", "description": "Telorgon automatic local desktop session" if normal else "Experimental system services and regular-user PAM session qualification",
                "licenses": ["MIT"], "depends": ["custom-distro-base", "systemd", "dbus", "pam", "util-linux"],
                "backup": [path.relative_to(stage).as_posix() for path in sorted((stage / "etc").rglob("*")) if path.is_file() and not path.is_symlink()]}
    if desktop:
        metadata["depends"] += ["telorgon-shell", "wayland", "networkmanager", "polkit", "upower", "power-profiles-daemon"]
    return export_package(stage, project / "out/packages/custom-distro-session" / identity, metadata,
                          source_date_epoch=1756684800, provenance={"source_kind": "catalog-local", "system_identity": identity,
                          "desktop_probe": probe, "scope": "automatic local desktop; locked accounts; no qualification clients" if normal else "private VM session qualification; locked accounts; no enrolled login password"}).path


def console_image(project: Path, existing_root: Path | None = None, *, test_upgrade: bool = False,
                  test_signed_upgrade: bool = False, test_pam_auth: bool = False, profile_name: str = "console") -> dict:
    locks = project / "out/locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / "image.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _compose_image(project, existing_root, test_upgrade=test_upgrade,
                              test_signed_upgrade=test_signed_upgrade, test_pam_auth=test_pam_auth, profile_name=profile_name)


def _compose_image(project: Path, existing_root: Path | None = None, *, test_upgrade: bool = False,
                   test_signed_upgrade: bool = False, test_pam_auth: bool = False, profile_name: str = "console") -> dict:
    output = project / "out"
    if profile_name not in {"console", "systemd", "desktop", "desktop-use", "desktop-dev"}:
        raise BuildError(f"unsupported image profile: {profile_name}")
    if profile_name != "console" and (test_upgrade or test_signed_upgrade):
        raise BuildError("package upgrade fixtures currently require the console profile")
    if test_pam_auth and profile_name != "systemd":
        raise BuildError("password-authentication fixture requires --profile systemd")
    profile = tomllib.loads((project / f"profiles/{profile_name}.toml").read_text())
    if profile.get("schema") != 1:
        raise BuildError("unsupported console profile schema")
    if sum((test_upgrade, test_signed_upgrade, test_pam_auth)) > 1:
        raise BuildError("choose one qualification fixture per disposable image")
    selected = list(profile["packages"]) + (["gnupg"] if test_signed_upgrade else [])
    artifacts = [_artifact(project, name) for name in runtime_packages(project, selected,
                 providers={"libudev": "systemd"} if profile_name != "console" else {})]
    base = base_package(project, profile_name)
    policy = system_policy_package(project, profile_name) if profile_name != "console" else None
    loader = output / "loader/cargo-target/x86_64-unknown-uefi/release/boot-efi.efi"
    validate_efi(loader)
    manifest = {"name": "Custom Distro", "version": "0.1.0", "profile": f"experimental-{profile_name}",
                "target": "x86_64-custom-linux-gnu",
                "packages": [{"name": item["package"], "sha256": item["sha256"],
                              "version": item["package_metadata"]["pkgver"],
                              "runtime_dependencies": item["package_metadata"].get("depend", []),
                              "licenses": item["package_metadata"].get("license", []),
                              "build_identity": item["build_identity"],
                              "sources": _source_summary(item)} for item in artifacts],
                "loader_sha256": sha256(loader),
                "base_package_sha256": sha256(base), "test_upgrade": test_upgrade,
                "test_signed_upgrade": test_signed_upgrade,
                "test_pam_auth": test_pam_auth,
                "package_trust_policy": "signed-ephemeral-test-key" if test_signed_upgrade else "unsigned-local-development",
                "session_policy_sha256": sha256(policy) if policy else None,
                "image_ownership": {"home/custom": [1000, 1000]} if policy else {},
                "qualification": "normal desktop session; separate exact-image qualification required" if profile_name in {"desktop-use", "desktop-dev"} else "runtime checks must be run against this exact image digest",
                "capabilities": {"telorgon_bootloader": True, "source_built_libc": True,
                                 "persistent_root": True, "alpm_ownership_database": True,
                                 "systemd": policy is not None, "desktop": "automatic-local-session" if profile_name in {"desktop-use", "desktop-dev"} else "qualification-required" if profile_name == "desktop" else False,
                                 "screen_cast": False, "graphics": "cpu-drm-kms" if profile_name in {"desktop", "desktop-use", "desktop-dev"} else "console",
                                 "signed_updates": test_signed_upgrade, "development_agent": profile_name == "desktop-dev",
                                 "installer": False, "independent_recovery": False}}
    toolkit = PacmanToolkit(output / "native-toolkit/prefix")
    fixture = None
    if test_upgrade:
        from .packaging.guest_test import prepare_upgrade_fixture
        if existing_root:
            raise BuildError("an upgrade fixture requires a fresh disposable root")
        fixture = prepare_upgrade_fixture(output / "work/guest-upgrade", toolkit)
        manifest["upgrade_fixture"] = fixture
    elif test_signed_upgrade:
        from .packaging.signed_test import prepare_signed_fixture
        if existing_root:
            raise BuildError("a signed fixture requires a fresh disposable root")
        fixture_identity = sha256(Path(__file__).parent / "packaging/signed_test.py")[:16]
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        fixture = prepare_signed_fixture(output / "signing-tests" / f"guest-{fixture_identity}-{day}", toolkit)
        manifest["signed_upgrade_fixture"] = fixture
    elif test_pam_auth:
        from .packaging.pam_test import prepare_pam_fixture
        if existing_root:
            raise BuildError("password-authentication fixture requires a fresh disposable root")
        fixture_identity = sha256(Path(__file__).parent / "packaging/pam_test.py")[:16]
        qualifier_identity = sha256(output / "qualification/pam-auth-probe/current.json")[:16]
        fixture = prepare_pam_fixture(output / "work" / f"guest-pam-auth-{fixture_identity}-{qualifier_identity}", toolkit)
        manifest["pam_auth_fixture"] = fixture
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    root = existing_root.resolve() if existing_root else _fresh(output / f"roots/{profile_name}" / identity, project)
    if not root.is_relative_to(output):
        raise BuildError("console root must be an explicitly generated project output")
    archives = [Path(item["path"]) for item in artifacts] + [base] + ([policy] if policy else [])
    if fixture:
        archives.append(Path(fixture["initial_package"]))
    if not existing_root:
        toolkit.install(root, archives, bootstrap=True,
                        expected_hashes={path.name: sha256(path) for path in archives})
    if test_upgrade:
        from .packaging.guest_test import stage_upgrade_fixture
        stage_upgrade_fixture(root, fixture, toolkit)
    if test_signed_upgrade:
        from .packaging.signed_test import stage_signed_fixture
        stage_signed_fixture(root, fixture, toolkit)
    elif test_pam_auth:
        from .packaging.pam_test import stage_pam_fixture
        stage_pam_fixture(root, fixture, toolkit)
    if not test_signed_upgrade:
        from .packaging.signed_test import configure_development
        configure_development(root)
    for relative in ("bin/busybox", "etc/os-release", "usr/lib/libc.so.6", "usr/lib/custom-distro/init"):
        if not (root / relative).is_file():
            raise BuildError(f"assembled console root is missing {relative}")
    manifest_path = root / "etc/custom-distro/build-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    inventory = {"schema": 1, "format": "custom-distro-package-inventory", "image_build_identity": identity,
                 "packages": manifest["packages"], "local_policy": {
                     "base_package_sha256": sha256(base), "session_policy_sha256": sha256(policy) if policy else None},
                 "security_support": "experimental inputs; vulnerability review and maintenance policy required before release"}
    (root / "etc/custom-distro/package-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    print(toolkit.query(root), flush=True)
    snapshot = output / f"repository/{profile_name}" / identity
    if not snapshot.exists():
        toolkit.create_repository(snapshot, archives)
    early = _fresh(output / "roots/initramfs" / identity, project)
    # Re-extract the verified package archive. Mutable build staging trees never
    # supply a boot payload after the package has been exported.
    busybox_archive = Path(next(item["path"] for item in artifacts if item["package"] == "busybox"))
    with tarfile.open(busybox_archive) as package:
        payload = [member for member in package if not member.name.lstrip("./").startswith(("PKGINFO", "BUILDINFO", "MTREE", "CUSTOM_PROVENANCE"))]
        package.extractall(early, members=payload, filter="data")
    shutil.copy2(project / "system/init", early / "init")
    early.joinpath("init").chmod(0o755)
    initramfs = build_initramfs(early, output / "boot" / identity / "console-initramfs.img", epoch=1756684800)
    kernels = list((root / "boot").glob("vmlinuz-*"))
    if len(kernels) != 1:
        raise BuildError("kernel package must contain exactly one EFI kernel")
    esp = _fresh(output / f"esp/{profile_name}" / identity, project)
    uuid = root_uuid(root)
    boot = stage_esp(loader, kernels[0], initramfs, esp, root_uuid=uuid)
    image_name = "custom-distro-pam-auth-test.img" if test_pam_auth else "custom-distro-signed-update-test.img" if test_signed_upgrade else "custom-distro-update-test.img" if test_upgrade else f"custom-distro-{profile_name}.img" if policy else "custom-distro.img"
    image = build_disk(esp, root, output / "images" / f"candidate-{identity}.img", size_mib=profile.get("image_size_mib", 1024), root_uuid=uuid,
                       ownership={"home/custom": (1000, 1000)} if policy else None)
    # Retain the exact qualified bytes when the convenient current alias moves.
    candidate = Path(image["path"])
    objects = output / "images/objects"
    objects.mkdir(exist_ok=True)
    retained = objects / f"{image['sha256']}.img"
    if retained.exists():
        if retained.is_symlink() or sha256(retained) != image["sha256"]:
            raise BuildError("retained image differs from its content identity")
        candidate.unlink()
    else:
        candidate.replace(retained)
    retained.chmod(0o600 if test_pam_auth else 0o444)
    Path(image["manifest"]).unlink()
    image.update({"path": str(retained), "image": str(retained), "current_alias": str(output / "images" / image_name),
                  "manifest": str(retained.with_suffix(".img.json"))})
    Path(image["manifest"]).write_text(json.dumps(image, indent=2) + "\n")
    alias = output / "images" / image_name
    temporary_alias = alias.with_suffix(".next")
    if alias.is_symlink() or temporary_alias.exists() or temporary_alias.is_symlink():
        raise BuildError("current image alias must be an ordinary managed file")
    os.link(retained, temporary_alias)
    temporary_alias.replace(alias)
    report = {"identity": identity, "root": str(root), "repository": str(snapshot),
              "boot": boot, "image": image, "manifest": manifest, "native_toolkit": toolkit.record}
    record = output / "images" / ("pam-auth-build.json" if test_pam_auth else "signed-update-build.json" if test_signed_upgrade else "update-build.json" if test_upgrade else f"{profile_name}-build.json")
    record.write_text(json.dumps(report, indent=2) + "\n")
    record.with_name(record.stem + "-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return report
