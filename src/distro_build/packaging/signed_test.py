"""Ephemeral signing fixtures for standard ALPM validation in disposable guests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

from .archive import export_package, inspect_package
from .toolkit import PacmanToolkit, ToolkitError


NAME = "custom-distro-signed-update-fixture"
UNSIGNED = "custom-distro-signature-unsigned"
TAMPERED = "custom-distro-signature-tampered"
CONFIG = "etc/custom-distro-signed-update-fixture.conf"
PAYLOAD = "usr/share/custom-distro-signed-update-fixture/payload"
CHECK = "usr/libexec/custom-distro/check-signed-package-upgrade"
PUBLIC = "usr/share/custom-distro/signing-test/public-key.asc"
OWNERTRUST = "usr/share/custom-distro/signing-test/ownertrust.txt"
ADMIN_CONFIG = "administrator=preserved\n"

STRICT_CONFIG = """# Disposable signed integration fixture; public test key only.
[options]
Architecture = x86_64
SigLevel = Required DatabaseRequired
LocalFileSigLevel = Required
GPGDir = /etc/pacman.d/gnupg

[distro]
Server = file:///var/lib/custom-distro/repository
"""

DEVELOPMENT_CONFIG = """# Explicit unsigned local development policy. Never publish this policy.
[options]
Architecture = x86_64
SigLevel = Never
LocalFileSigLevel = Never
XferCommand = /usr/libexec/custom-distro/local-fetch %u %o

[distro]
Server = file:///var/lib/custom-distro/repository
"""

CHECK_SCRIPT = """#!/bin/sh
# Installed only by the disposable signed update fixture package.
set -eu
umask 077
keyring=/etc/pacman.d/gnupg
mkdir -p "$keyring"
chmod 700 "$keyring"
gpg --no-options --batch --homedir "$keyring" --no-default-keyring --keyring "$keyring/pubring.gpg" --import /usr/share/custom-distro/signing-test/public-key.asc
gpg --no-options --batch --homedir "$keyring" --import-ownertrust /usr/share/custom-distro/signing-test/ownertrust.txt
gpg --no-options --batch --homedir "$keyring" --check-trustdb
package=custom-distro-signed-update-fixture
configuration=/etc/custom-distro-signed-update-fixture.conf
payload=/usr/share/custom-distro-signed-update-fixture/payload
[ "$(pacman -Q "$package")" = "$package 1.0-1" ]
[ "$(pacman -Qoq "$configuration")" = "$package" ]
[ "$(cat "$configuration")" = 'administrator=preserved' ]
pacman -Syu --noconfirm
[ "$(pacman -Q "$package")" = "$package 1.0-2" ]
[ "$(pacman -Qoq "$payload")" = "$package" ]
[ "$(cat "$payload")" = 'signed-updated' ]
[ "$(cat "$configuration")" = 'administrator=preserved' ]
[ "$(cat "$configuration.pacnew")" = 'default=revision2' ]
echo CUSTOM_SIGNED_UPGRADE_ACCEPTED
if pacman -S --noconfirm custom-distro-signature-unsigned > /run/unsigned-package.log 2>&1; then
    cat /run/unsigned-package.log
    echo 'unsigned package incorrectly accepted' >&2
    exit 1
fi
cat /run/unsigned-package.log
# ALPM requires the detached signature and curl reports that exact absent file.
[ -f /var/lib/custom-distro/repository/custom-distro-signature-unsigned-1.0-1-any.pkg.tar.xz ]
grep -q "failed retrieving file 'custom-distro-signature-unsigned-1.0-1-any.pkg.tar.xz.sig'" /run/unsigned-package.log
grep -q 'failed to commit transaction' /run/unsigned-package.log
if pacman -U --noconfirm /var/lib/custom-distro/repository/custom-distro-signature-unsigned-1.0-1-any.pkg.tar.xz > /run/unsigned-local-package.log 2>&1; then
    cat /run/unsigned-local-package.log
    echo 'unsigned local package incorrectly accepted' >&2
    exit 1
fi
cat /run/unsigned-local-package.log
grep -qi 'missing required signature' /run/unsigned-local-package.log
if pacman -Q custom-distro-signature-unsigned > /dev/null 2>&1; then exit 1; fi
[ ! -e /usr/share/custom-distro-signature-unsigned/payload ]
echo CUSTOM_UNSIGNED_PACKAGE_REJECTED
if pacman -S --noconfirm custom-distro-signature-tampered > /run/tampered-package.log 2>&1; then
    cat /run/tampered-package.log
    echo 'tampered package incorrectly accepted' >&2
    exit 1
fi
cat /run/tampered-package.log
grep -qi 'PGP signature' /run/tampered-package.log
if pacman -Q custom-distro-signature-tampered > /dev/null 2>&1; then exit 1; fi
[ ! -e /usr/share/custom-distro-signature-tampered/payload ]
echo CUSTOM_TAMPERED_PACKAGE_REJECTED
# A required database signature must also be present, even if packages are signed.
rm -f /var/lib/pacman/sync/distro.db /var/lib/pacman/sync/distro.db.sig
rm -f /var/lib/custom-distro/repository/distro.db.sig /var/lib/custom-distro/repository/distro.db.tar.gz.sig
if pacman -Sy --noconfirm > /run/unsigned-database.log 2>&1; then
    cat /run/unsigned-database.log
    echo 'unsigned database incorrectly accepted' >&2
    exit 1
fi
cat /run/unsigned-database.log
grep -q "failed retrieving file 'distro.db.sig'" /run/unsigned-database.log
grep -q 'failed to synchronize all databases' /run/unsigned-database.log
echo CUSTOM_UNSIGNED_DATABASE_REJECTED
echo CUSTOM_SIGNED_PACKAGE_UPGRADE_OK
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(toolkit: PacmanToolkit) -> Path:
    project = toolkit.prefix.parents[2]
    if toolkit.prefix != project / "out/native-toolkit/prefix":
        raise ToolkitError("signing fixtures require the project's private native toolkit")
    return project


def _run(command: list[str], environment: dict[str, str]) -> str:
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    if result.returncode:
        raise ToolkitError(f"private signing fixture command failed ({result.returncode}): {command[0]}\n{result.stdout[-2000:]}{result.stderr[-4000:]}")
    return result.stdout


def configure_development(root: Path) -> dict:
    """Explicitly choose the unsigned policy for a generated disposable root."""
    root = Path(root).resolve()
    project = Path(__file__).resolve().parents[3]
    if not root.is_relative_to(project / "out/roots"):
        raise ToolkitError("development policy requires a generated project output root")
    configuration = root / "etc/pacman.conf"
    if not configuration.is_file() or configuration.is_symlink():
        raise ToolkitError("development policy requires an installed ordinary pacman config")
    configuration.write_text(DEVELOPMENT_CONFIG)
    return {"trust_policy": "unsigned-local-development", "signatures_required": False,
            "configuration_sha256": _sha(configuration), "generated_root_only": True}


def prepare_signed_fixture(directory: Path, toolkit: PacmanToolkit) -> dict:
    """Create signed test artifacts, delete private key, retain public receipts.

    The host gpg executable is an explicitly recorded fixture generator. Actual
    acceptance/rejection is performed by the source-built guest GPGME/ALPM stack.
    No private key, signing socket, or owner secret enters a package or image.
    """
    project = _project(toolkit)
    directory = Path(directory).resolve()
    if not directory.is_relative_to(project / "out/signing-tests"):
        raise ToolkitError("signing fixture outputs must remain below out/signing-tests")
    identity = _sha(Path(__file__))
    manifest = directory / "fixture.json"
    if manifest.is_file():
        prior = json.loads(manifest.read_text())
        if prior.get("fixture_identity") != identity:
            raise ToolkitError("signed fixture generator changed; use a fresh directory")
        for filename, expected in prior["artifact_sha256"].items():
            artifact = directory / filename
            if not artifact.is_file() or _sha(artifact) != expected:
                raise ToolkitError(f"signed fixture artifact differs: {filename}")
        return prior
    if directory.exists():
        raise ToolkitError("incomplete signed fixture exists; use a fresh directory")
    directory.mkdir(parents=True, mode=0o700)
    gpg = Path("/usr/bin/gpg")
    gpgconf = Path("/usr/bin/gpgconf")
    if not gpg.is_file() or not gpgconf.is_file():
        raise ToolkitError("declared host gpg fixture generator is unavailable")
    signer = project / "out/signing-tests" / ("key-" + uuid.uuid4().hex[:8])
    signer.mkdir(mode=0o700)
    environment = toolkit._env()
    environment["GNUPGHOME"] = str(signer)
    public, ownertrust = directory / "public-key.asc", directory / "ownertrust.txt"
    common = [str(gpg), "--homedir", str(signer), "--no-options", "--batch", "--pinentry-mode", "loopback", "--passphrase", ""]
    try:
        _run([*common, "--quick-generate-key", "Custom Distro disposable signing fixture", "ed25519", "sign", "1d"], environment)
        key_listing = _run([*common, "--with-colons", "--list-keys"], environment)
        fingerprint = next(line.split(":")[9] for line in key_listing.splitlines() if line.startswith("fpr:"))
        public.write_text(_run([*common, "--armor", "--export", fingerprint], environment))
        ownertrust.write_text(f"{fingerprint}:6:\n")
        public.chmod(0o644)
        ownertrust.chmod(0o644)
        stage = directory / "stage"
        for filename in (CONFIG, PAYLOAD, CHECK, PUBLIC, OWNERTRUST):
            (stage / filename).parent.mkdir(parents=True, exist_ok=True)
        (stage / CHECK).write_text(CHECK_SCRIPT)
        (stage / CHECK).chmod(0o755)
        shutil.copyfile(public, stage / PUBLIC)
        shutil.copyfile(ownertrust, stage / OWNERTRUST)
        metadata = {"name": NAME, "version": "1.0", "revision": 1, "arch": "any",
                    "description": "Disposable signed ALPM upgrade integration fixture",
                    "licenses": ["MIT"], "depends": ["pacman", "gnupg"], "backup": [CONFIG]}
        provenance = {"kind": "disposable-signature-fixture", "fixture_identity": identity}
        (stage / CONFIG).write_text("default=revision1\n")
        (stage / PAYLOAD).write_text("initial\n")
        def normalize(payload_stage: Path) -> None:
            for path in payload_stage.rglob("*"):
                path.chmod(0o755 if path.is_dir() or path.relative_to(payload_stage).as_posix() == CHECK else 0o644)
            payload_stage.chmod(0o755)
        normalize(stage)
        initial = export_package(stage, directory / "packages", metadata,
                                 source_date_epoch=1756684800, provenance=provenance)
        metadata["revision"] = 2
        (stage / CONFIG).write_text("default=revision2\n")
        (stage / PAYLOAD).write_text("signed-updated\n")
        upgrade = export_package(stage, directory / "packages", metadata,
                                 source_date_epoch=1756684800, provenance=provenance)
        def simple(name: str, value: str, destination: str):
            payload_stage = directory / destination / "stage"
            payload = payload_stage / f"usr/share/{name}/payload"
            payload.parent.mkdir(parents=True)
            payload.write_text(value)
            normalize(payload_stage)
            return export_package(payload_stage, directory / destination / "packages",
                                  {"name": name, "version": "1.0", "revision": 1, "arch": "any",
                                   "description": "Disposable negative signature test", "licenses": ["MIT"],
                                   "depends": ["pacman"]}, source_date_epoch=1756684800, provenance=provenance)
        unsigned = simple(UNSIGNED, "must-not-install\n", "unsigned")
        original = simple(TAMPERED, "valid-original\n", "valid-original")
        tampered = simple(TAMPERED, "changed-after-signing\n", "tampered")
        def sign(path: Path) -> Path:
            signature = Path(str(path) + ".sig")
            _run([*common, "--local-user", fingerprint, "--output", str(signature), "--detach-sign", str(path)], environment)
            return signature
        for package in (initial, upgrade, original):
            sign(package.path)
        shutil.copyfile(Path(str(original.path) + ".sig"), Path(str(tampered.path) + ".sig"))
        repository = toolkit.create_repository(directory / "repository", [upgrade.path, unsigned.path, tampered.path])
        repo = directory / "repository"
        for package in (upgrade, tampered):
            shutil.copyfile(Path(str(package.path) + ".sig"), repo / (package.path.name + ".sig"))
        database = Path(repository["database"])
        _run([str(toolkit.prefix / "bin/repo-add"), "--include-sigs", "--sign", "--key", fingerprint,
              str(database), *[str(repo / package.path.name) for package in (upgrade, unsigned, tampered)]], environment)
        for old in repo.glob("*.old"):
            old.unlink()
        # Verify signature validity and deliberate invalidity independently of ALPM.
        positive = _run([*common, "--status-fd", "1", "--verify", str(repo / (upgrade.path.name + ".sig")), str(repo / upgrade.path.name)], environment)
        _run([*common, "--verify", str(database) + ".sig", str(database)], environment)
        negative = subprocess.run([*common, "--status-fd", "1", "--verify", str(repo / (tampered.path.name + ".sig")), str(repo / tampered.path.name)], env=environment, capture_output=True, text=True)
        if "[GNUPG:] VALIDSIG " not in positive or negative.returncode == 0 or "[GNUPG:] BADSIG " not in negative.stdout:
            raise ToolkitError("signed fixture does not contain the intended valid/bad signatures")
        key_record = next(line.split(":") for line in key_listing.splitlines() if line.startswith("pub:"))
        record = {"schema": 1, "development_only": True, "trust_policy": "signed-ephemeral-test-key",
                  "name": NAME, "fixture_identity": identity, "initial_version": "1.0-1", "upgrade_version": "1.0-2",
                  "initial_package": str(initial.path), "upgrade_package": str(upgrade.path),
                  "unsigned_package": str(unsigned.path), "tampered_package": str(tampered.path),
                  "repository": str(repo), "database": str(database), "public_key": str(public),
                  "ownertrust": str(ownertrust), "public_fingerprint": fingerprint,
                  "public_key_expires_at": int(key_record[6]), "private_key_bundled": False,
                  "profile_packages": ["pacman", "gnupg"], "guest_check": "/" + CHECK,
                  "success_marker": "CUSTOM_SIGNED_PACKAGE_UPGRADE_OK",
                  "fixture_generator": {"path": str(gpg), "sha256": _sha(gpg),
                                        "version": _run([str(gpg), "--version"], environment).splitlines()[0]},
                  "tampered_archive_matches_database_digest": True,
                  "tampered_archive_has_original_invalid_signature": True}
    finally:
        # Contact only the agent associated with this explicit private homedir.
        subprocess.run([str(gpgconf), "--homedir", str(signer), "--kill", "gpg-agent"], env=environment,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(signer)
    record["private_signer_removed"] = not signer.exists()
    record["artifact_sha256"] = {str(path.relative_to(directory)): _sha(path)
                                 for path in directory.rglob("*") if path.is_file() and not path.is_symlink()}
    manifest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def stage_signed_fixture(root: Path, fixture: dict, toolkit: PacmanToolkit) -> dict:
    """Stage public-only signed test input after installing initial fixture."""
    project = _project(toolkit)
    root = Path(root).resolve()
    if not root.is_relative_to(project / "out/roots"):
        raise ToolkitError("signed fixture requires a generated project output root")
    directory = Path(fixture["public_key"]).parent
    for filename, expected in fixture["artifact_sha256"].items():
        artifact = directory / filename
        if not artifact.is_file() or _sha(artifact) != expected:
            raise ToolkitError(f"signed fixture artifact differs: {filename}")
    if f"{NAME} 1.0-1" not in toolkit.query(root, NAME).splitlines():
        raise ToolkitError("initial signed fixture package must already be installed")
    config = root / CONFIG
    if config.read_text() not in {"default=revision1\n", ADMIN_CONFIG}:
        raise ToolkitError("unexpected signed fixture administrator configuration")
    config.write_text(ADMIN_CONFIG)
    destination = root / "var/lib/custom-distro/repository"
    if destination.exists():
        raise ToolkitError("signed test repository already exists; use a fresh root")
    shutil.copytree(Path(fixture["repository"]), destination, symlinks=True)
    configuration = root / "etc/pacman.conf"
    if not configuration.is_file() or configuration.is_symlink():
        raise ToolkitError("signed fixture needs an installed ordinary pacman configuration")
    configuration.write_text(STRICT_CONFIG)
    return {"fixture": fixture, "trust_policy": "signed-ephemeral-test-key",
            "repository_in_guest": "/var/lib/custom-distro/repository", "private_key_bundled": False,
            "configuration_sha256": _sha(configuration), "administrator_config_sha256": _sha(config),
            "guest_check": fixture["guest_check"], "success_marker": fixture["success_marker"]}
