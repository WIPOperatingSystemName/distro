"""Portable, digest-bound Telorgon workspace inputs for independent CI workers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import tarfile
import tempfile

from .apps import INPUTS, _copy_inputs
from .model import BuildError
from .sources import extract, sha256


BUNDLE_INPUTS = {**INPUTS, "telorgon-bootloader": ("Cargo.toml", "Cargo.lock", "LICENSE", "README.md", "crates", "configs", "tools")}


def export_bundle(project: Path, source_root: Path | None = None) -> dict:
    project = project.resolve()
    source_root = (source_root or project / "sources").resolve()
    output = project / "out/bundles"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="export-", dir=output) as temporary:
        workspace = Path(temporary)
        sources = {}
        for name, inputs in BUNDLE_INPUTS.items():
            record = _copy_inputs(source_root / name, workspace / name, inputs)
            # Match tarfile's data-filter mode policy, preserving source bytes
            # and owner execution while removing writable/SUID transport bits.
            record["original_identity"] = record["identity"]
            for item in record["files"]:
                original = item["mode"]
                mode = (original & 0o755) | 0o600
                if not original & 0o100:
                    mode &= ~0o111
                item["original_mode"] = original
                item["mode"] = mode
                (workspace / name / item["path"]).chmod(mode)
            record["identity"] = hashlib.sha256(json.dumps(record["files"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            sources[name] = {key: value for key, value in record.items() if key != "source"}
        body = {"schema": 1, "kind": "telorgon-source-workspace", "sources": sources}
        identity = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        body["identity"] = identity
        (workspace / "source-manifest.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
        archive = output / f"telorgon-sources-{identity}.tar.xz"
        candidate = output / f".{identity}.tmp"
        try:
            with tarfile.open(candidate, "w:xz", format=tarfile.PAX_FORMAT) as stream:
                for path in sorted(workspace.rglob("*")):
                    info = stream.gettarinfo(str(path), arcname=path.relative_to(workspace).as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mtime = 1756684800
                    info.pax_headers = {}
                    if path.is_file():
                        with path.open("rb") as payload:
                            stream.addfile(info, payload)
                    else:
                        stream.addfile(info)
            if archive.exists() and sha256(archive) != sha256(candidate):
                raise BuildError("source bundle identity has different archive bytes")
            candidate.replace(archive)
        finally:
            candidate.unlink(missing_ok=True)
    result = {"schema": 1, "identity": identity, "archive": str(archive), "sha256": sha256(archive), "manifest": body}
    (output / "export.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def verify_workspace(root: Path) -> dict:
    root = root.resolve(strict=True)
    document = root / "source-manifest.json"
    if document.stat().st_size > 16 * 1024 * 1024:
        raise BuildError("workspace source manifest exceeds its size limit")
    manifest = json.loads(document.read_text())
    if manifest.get("schema") != 1 or manifest.get("kind") != "telorgon-source-workspace":
        raise BuildError("unsupported workspace source manifest")
    if set(manifest["sources"]) != set(BUNDLE_INPUTS):
        raise BuildError("workspace source project set differs from the catalog")
    body = {key: value for key, value in manifest.items() if key != "identity"}
    identity = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if identity != manifest["identity"]:
        raise BuildError("workspace source manifest identity differs")
    expected = {"source-manifest.json"}
    for name, source in manifest["sources"].items():
        records = source["files"]
        recorded_identity = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if recorded_identity != source["identity"]:
            raise BuildError(f"workspace source record differs: {name}")
        for record in records:
            relative = Path(record["path"])
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() in {"", "."}:
                raise BuildError("unsafe workspace source path")
            path = root / name / relative
            if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root)
                    or path.stat().st_size != record["size"] or stat.S_IMODE(path.stat().st_mode) != record["mode"]
                    or sha256(path) != record["sha256"]):
                raise BuildError(f"workspace source file differs: {name}/{relative}")
            full = f"{name}/{relative.as_posix()}"
            if full in expected:
                raise BuildError("duplicate workspace source record")
            expected.add(full)
        if sha256(root / name / "Cargo.lock") != source["cargo_lock_sha256"]:
            raise BuildError(f"workspace Cargo lock differs: {name}")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() or path.is_symlink()}
    if actual != expected:
        raise BuildError("workspace contains missing or unrecorded files")
    return manifest


def import_bundle(project: Path, archive: Path, expected_sha256: str) -> dict:
    project, archive = project.resolve(), archive.resolve(strict=True)
    if len(expected_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_sha256):
        raise BuildError("a verified lowercase SHA256 is required for source bundle import")
    if sha256(archive) != expected_sha256:
        raise BuildError("workspace archive differs from its expected SHA256")
    output = project / "out/bundles"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / expected_sha256
    if destination.exists():
        manifest = verify_workspace(destination)
    else:
        with tempfile.TemporaryDirectory(prefix="import-", dir=output) as temporary:
            root = extract(archive, Path(temporary) / "workspace")
            manifest = verify_workspace(root)
            root.rename(destination)
    result = {"schema": 1, "root": str(destination), "identity": manifest["identity"], "archive_sha256": expected_sha256,
              "origin": "verified-portable-source-bundle", "manifest": str(destination / "source-manifest.json")}
    (output / "current.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
