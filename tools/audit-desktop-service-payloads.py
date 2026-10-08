#!/usr/bin/env python3
"""Audit canonical service archives for build paths in guest runtime payloads."""
from pathlib import Path
import hashlib
import json
import sys
import tarfile

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))
from distro_build.desktop_services import ServiceBuild

names = ["pcre2", "lua", "duktape", "glib", "libgudev", "wireplumber", "polkit", "upower",
         "power-profiles-daemon", "bubblewrap", "fuse3", "libpng", "json-glib", "gdk-pixbuf",
         "gstreamer", "gst-plugins-base", "gst-plugins-good", "xdg-utils", "wl-clipboard", "xdg-desktop-portal"]
records = [json.loads((project / "out/state/packages" / (name + ".json")).read_text()) for name in names]
identity = hashlib.sha256(json.dumps({"archives": {r["package"]: r["sha256"] for r in records},
    "auditor": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "implementation": hashlib.sha256((project / "src/distro_build/desktop_services.py").read_bytes()).hexdigest()}, sort_keys=True).encode()).hexdigest()
work = project / "out/qualification/desktop-payload-paths" / identity
work.mkdir(parents=True, exist_ok=True)
reports = []
for record in records:
    archive = Path(record["path"])
    if hashlib.sha256(archive.read_bytes()).hexdigest() != record["sha256"]:
        raise RuntimeError("Canonical package hash mismatch: " + record["package"])
    directory = work / record["package"]
    directory.mkdir(exist_ok=True)
    stage = directory / "payload"
    stage.mkdir(exist_ok=True)
    modes = {}
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeError("Unsafe canonical package member: " + member.name)
            if member.mode & 0o6000:
                modes[member.name] = oct(member.mode)
        stream.extractall(stage, filter="data")
    audit = object.__new__(ServiceBuild)
    audit.work = directory
    audit.stage = stage
    audit.audit_payload_paths()
    report = json.loads((directory / "target-payload-path-audit.json").read_text())
    reports.append({"package": record["package"], "archive_sha256": record["sha256"],
                    "build_identity": record["build_identity"], "privileged_helpers": modes,
                    "audit": report})
report = {"passed": True, "identity": identity, "kind": "canonical-service-archive-runtime-path-audit",
          "packages": reports, "scope": "All archive member paths and symlinks, text/data and allocated ELF constants; diagnostic source filenames and nonallocated debug source paths are distinguished from runtime lookups"}
(work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"passed": True, "packages": len(reports), "report": str(work / "report.json")}, indent=2))
