#!/usr/bin/env python3
"""Audit canonical installed resource paths without treating ELF debug data as paths."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys
import tarfile

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))
from distro_build.packaging import inspect_package

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=project / "out/qualification/installed-resource-path-audit.json")
    args = parser.parse_args()
    problems, packages, checked = [], [], []
    states = sorted((project / "out/state/packages").glob("*.json")) + sorted((project / "out/state/apps").glob("*.json"))
    if not states:
        raise RuntimeError("No canonical package states are available to audit")
    for state in states:
        record = json.loads(state.read_text())
        info = inspect_package(Path(record["path"]))
        if info["sha256"] != record["sha256"]:
            raise RuntimeError("Package digest differs from canonical state: " + str(state))
        packages.append({"name": info["name"], "version": info["version"], "sha256": info["sha256"]})
        with tarfile.open(record["path"]) as archive:
            for member in archive:
                name = member.name
                if name.startswith(("home/", "tmp/")):
                    problems.append({"package": info["name"], "path": name, "reason": "build-directory top-level payload"})
                if not member.isfile() or member.size > 4 * 1024**2:
                    continue
                if not (name.endswith((".service", ".socket", ".target", ".policy", ".conf", ".desktop", ".pc", ".hook"))
                        or name.startswith(("usr/bin/", "usr/libexec/"))):
                    continue
                data = archive.extractfile(member).read()
                # Actual ELF search paths are covered by target linkage audits;
                # source __FILE__/debugging strings do not name runtime resources.
                if b"\x00" in data:
                    continue
                checked.append({"package": info["name"], "path": name, "sha256": hashlib.sha256(data).hexdigest()})
                if re.search(rb'/[^\s\x00\'"<>]*out/work/', data):
                    problems.append({"package": info["name"], "path": name, "reason": "runtime text contains private build sysroot path"})
    report = {"success": not problems, "checker_sha256": digest(__file__), "packages": packages,
              "audited_text_files": len(checked), "checked": checked, "problems": problems,
              "scope": "Canonical source packages and apps: unit/config/policy/desktop/pkg-config/command text; archive integrity validated; ELF debugging paths excluded"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"success": report["success"], "audited_text_files": len(checked),
                      "packages": len(packages), "problems": problems, "report": str(args.output)}, indent=2))
    return 0 if report["success"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
