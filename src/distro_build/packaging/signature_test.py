"""Source-built GnuPG detached-signature qualification without host libraries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from ..graph import plan
from ..model import catalog
from ..runtime_native import RuntimeBuild
from .toolkit import PacmanToolkit


def qualify_signatures(project: Path, directory: Path, fixture: dict) -> dict:
    """Verify positive and changed archives using only target GnuPG/libgcrypt.

    This supplements the real guest ALPM test. It deliberately uses gpgv, so it
    needs no agent and cannot accidentally invoke the host's /usr/bin/gpg engine.
    """
    project, directory = Path(project).resolve(), Path(directory).resolve()
    if not directory.is_relative_to(project / "out/signing-tests") or directory.exists():
        raise ValueError("Use a fresh qualification directory under out/signing-tests")
    directory.mkdir(parents=True, mode=0o700)
    packages, identities = [], {}
    for recipe in plan(catalog(project), ["gnupg"]):
        state = json.loads((project / "out/state/packages" / f"{recipe.name}.json").read_text())
        package = Path(state["path"])
        actual = hashlib.sha256(package.read_bytes()).hexdigest()
        if actual != state["sha256"]:
            raise ValueError(f"Target signature candidate changed: {package}")
        packages.append(package)
        identities[recipe.name] = {"path": str(package), "sha256": actual}
    root = directory / "root"
    toolkit = PacmanToolkit(project / "out/native-toolkit/prefix")
    toolkit.install(root, packages, bootstrap=True,
                    expected_hashes={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in packages})
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "GNUPGHOME": str(directory)}
    context = SimpleNamespace(stage=root, sysroot=root, work=directory, env=environment)
    loader = lambda path: RuntimeBuild.closed_loader(context, path)
    keyring = directory / "public-key.gpg"
    subprocess.run([*loader(root / "usr/bin/gpg"), "--no-options", "--batch", "--no-autostart",
                    "--output", str(keyring), "--dearmor", fixture["public_key"]],
                   env=environment, capture_output=True, check=True)
    verify = [*loader(root / "usr/bin/gpgv"), "--homedir", str(directory), "--keyring", str(keyring),
              "--status-fd", "1"]
    repository = Path(fixture["repository"])
    positive_path = repository / Path(fixture["upgrade_package"]).name
    negative_path = repository / Path(fixture["tampered_package"]).name
    def run(path: Path) -> subprocess.CompletedProcess:
        return subprocess.run([*verify, str(path) + ".sig", str(path)], env=environment,
                              capture_output=True, text=True)
    positive, negative, database = run(positive_path), run(negative_path), run(Path(fixture["database"]))
    if positive.returncode or database.returncode or "[GNUPG:] VALIDSIG " not in positive.stdout:
        raise RuntimeError(f"Target GnuPG failed a valid signature: {positive.stderr}\n{database.stderr}")
    if negative.returncode == 0 or "[GNUPG:] BADSIG " not in negative.stdout:
        raise RuntimeError("Target GnuPG failed to reject the changed signed package")
    for name, process in (("valid-package", positive), ("changed-package", negative), ("valid-database", database)):
        (directory / f"{name}.log").write_text(process.stdout + process.stderr)
    result = {"schema": 1, "target_verifier": True, "valid_package_accepted": True,
              "valid_database_accepted": True, "changed_package_rejected": True,
              "public_fingerprint": fixture["public_fingerprint"], "private_key_bundled": False,
              "artifacts": identities, "fixture_identity": fixture["fixture_identity"],
              "success_marker": "CUSTOM_TARGET_SIGNATURES_OK"}
    (directory / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    print("CUSTOM_TARGET_SIGNATURES_OK", flush=True)
    return result
