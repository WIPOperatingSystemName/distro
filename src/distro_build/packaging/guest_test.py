"""Real package-manager upgrade fixtures for disposable guest images."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from .archive import export_package, inspect_package
from .toolkit import PacmanToolkit, ToolkitError


NAME = "custom-distro-update-fixture"
CONFIG = "etc/custom-distro-update-fixture.conf"
PAYLOAD = "usr/share/custom-distro-update-fixture/payload"
CHECK = "usr/libexec/custom-distro/check-package-upgrade"
ADMIN_CONFIG = "administrator=preserved\n"

CHECK_SCRIPT = """#!/bin/sh
# Installed by the disposable integration-test fixture package.
set -eu
package=custom-distro-update-fixture
configuration=/etc/custom-distro-update-fixture.conf
payload=/usr/share/custom-distro-update-fixture/payload
[ "$(pacman -Q "$package")" = "$package 1.0-1" ]
[ "$(pacman -Qoq "$configuration")" = "$package" ]
[ "$(cat "$configuration")" = 'administrator=preserved' ]
pacman -Syu --noconfirm
[ "$(pacman -Q "$package")" = "$package 1.0-2" ]
[ "$(pacman -Qoq "$payload")" = "$package" ]
[ "$(cat "$payload")" = 'updated' ]
[ "$(cat "$configuration")" = 'administrator=preserved' ]
[ "$(cat "$configuration.pacnew")" = 'default=revision2' ]
echo CUSTOM_PACKAGE_UPGRADE_OK
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_upgrade_fixture(directory: Path, toolkit: PacmanToolkit) -> dict:
    """Return initial package + standard v2 repository for a disposable image.

    Include ``initial_package`` in the root's ordinary ALPM assembly transaction,
    together with the target pacman stack. Then call stage_upgrade_fixture.
    The repository intentionally contains only the upgrade fixture package.
    """
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "fixture.json"
    identity = hashlib.sha256((CHECK_SCRIPT + ADMIN_CONFIG).encode()).hexdigest()
    if manifest.exists():
        prior = json.loads(manifest.read_text())
        if prior.get("fixture_identity") != identity:
            raise ToolkitError("fixture generator changed; use a fresh candidate directory")
        for key in ("initial_package", "upgrade_package", "database"):
            path = Path(prior[key])
            if not path.is_file() or _sha(path) != prior["sha256"][key]:
                raise ToolkitError(f"guest fixture artifact digest differs: {key}")
        for key in ("initial_package", "upgrade_package"):
            inspect_package(Path(prior[key]))
        return prior
    stage = directory / "stage"
    for name in (CONFIG, PAYLOAD, CHECK):
        (stage / name).parent.mkdir(parents=True, exist_ok=True)
    (stage / CHECK).write_text(CHECK_SCRIPT)
    (stage / CHECK).chmod(0o755)
    metadata = {"name": NAME, "version": "1.0", "revision": 1, "arch": "any",
                "description": "Disposable guest package-manager upgrade assertion",
                "licenses": ["MIT"], "depends": ["pacman"], "backup": [CONFIG]}
    (stage / CONFIG).write_text("default=revision1\n")
    (stage / PAYLOAD).write_text("initial\n")
    initial = export_package(stage, directory / "packages", metadata, source_date_epoch=1756684800,
                             provenance={"kind": "guest-integration-fixture", "fixture_identity": identity})
    (stage / CONFIG).write_text("default=revision2\n")
    (stage / PAYLOAD).write_text("updated\n")
    metadata["revision"] = 2
    upgrade = export_package(stage, directory / "packages", metadata, source_date_epoch=1756684800,
                             provenance={"kind": "guest-integration-fixture", "fixture_identity": identity})
    repository = toolkit.create_repository(directory / "repository", [upgrade.path])
    record = {"schema": 1, "development_only": True, "name": NAME,
              "fixture_identity": identity, "initial_version": "1.0-1", "upgrade_version": "1.0-2",
              "initial_package": str(initial.path), "upgrade_package": str(upgrade.path),
              "repository": str(directory / "repository"), "database": repository["database"],
              "guest_check": "/" + CHECK, "success_marker": "CUSTOM_PACKAGE_UPGRADE_OK",
              "sha256": {"initial_package": initial.sha256, "upgrade_package": upgrade.sha256,
                         "database": _sha(Path(repository["database"]))}}
    manifest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def stage_upgrade_fixture(root: Path, fixture: dict, toolkit: PacmanToolkit) -> dict:
    """Seed admin modification and local repo after initial package installation.

    This function does not execute target programs. The package-owned guest check
    performs the real update when the disposable image boots.
    """
    root = Path(root).resolve()
    installed = toolkit.query(root, fixture["name"]).splitlines()
    if f"{fixture['name']} {fixture['initial_version']}" not in installed:
        raise ToolkitError("guest upgrade fixture's initial package must already be installed")
    configuration = root / CONFIG
    if configuration.read_text() not in {"default=revision1\n", ADMIN_CONFIG}:
        raise ToolkitError("refusing to replace an unexpected fixture administrator config")
    configuration.write_text(ADMIN_CONFIG)
    source = Path(fixture["repository"])
    if _sha(Path(fixture["database"])) != fixture["sha256"]["database"]:
        raise ToolkitError("guest repository database digest differs from prepared fixture")
    repository_package = source / Path(fixture["upgrade_package"]).name
    if _sha(repository_package) != fixture["sha256"]["upgrade_package"]:
        raise ToolkitError("guest repository package digest differs from prepared fixture")
    inspect_package(repository_package)
    destination = root / "var/lib/custom-distro/repository"
    if destination.exists():
        raise ToolkitError("guest fixture repository already exists; use a fresh image root")
    shutil.copytree(source, destination, symlinks=True)
    return {"fixture": fixture, "repository_in_guest": "/var/lib/custom-distro/repository",
            "administrator_config_sha256": _sha(configuration),
            "guest_check": fixture["guest_check"], "success_marker": fixture["success_marker"]}
