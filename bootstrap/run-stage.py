#!/usr/bin/env python3
"""Run verified source bootstrap stages without installing into the build host."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib

sys.dont_write_bytecode = True
from integrity import fingerprint, receipt, seed_identity, stage_artifacts


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent


def digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, algorithm).hexdigest()


def read_stamp(path: Path) -> dict:
    return json.loads(path.read_text())


def check_stamp(record: dict, name: str) -> None:
    if record.get("schema") != 2:
        raise ValueError(f"{name} has a legacy completion stamp; explicitly run --upgrade-stamps to record a current baseline")
    identity = record["identity"]
    current = stage_receipt(identity["stage"], [Path(path) for path in record["artifact_roots"]],
                            Path(identity["root"]), Path(identity["tools"]), identity["target"])
    if current != record["artifacts"] or fingerprint(current) != record["artifact_fingerprint"]:
        raise ValueError(f"{name} artifacts changed since their receipt; rebuild the stage and its consumers explicitly")


def stage_receipt(stage: dict, paths: list[Path], root: Path, tools: Path, target: str) -> list[dict]:
    mapping = {"sysroot": str(root), "tools": str(tools), "target": target}
    excluded = tuple(Path(raw.format(**mapping)) for raw in stage.get("artifact_excludes", []))
    return receipt(paths, exclude=excluded)


def source_checks(names: list[str], sources: dict, sources_dir: Path) -> None:
    for source_name in names:
        item = sources[source_name]
        archive = sources_dir / item["filename"]
        if not archive.is_file() or digest(archive) != item["sha256"]:
            raise ValueError(f"Missing or invalid source: {archive}")
        if "sha512" in item and digest(archive, "sha512") != item["sha512"]:
            raise ValueError(f"Upstream SHA512 mismatch: {archive}")
        if "reference_md5" in item and digest(archive, "md5") != item["reference_md5"]:
            raise ValueError(f"LFS reference mismatch: {archive}")


def write_stamp(path: Path, record: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(path)


def stage_identity(stage: dict, target: str, root: Path, tools: Path,
                   required: list[dict], sources: dict, engine: dict, seeds: dict) -> dict:
    script = (HERE / stage["script"]).resolve(strict=True)
    helpers = [(HERE / raw).resolve(strict=True) for raw in stage.get("helpers", [])]
    if not script.is_relative_to(HERE) or any(not helper.is_relative_to(HERE) for helper in helpers):
        raise ValueError("Stage script/helper escapes bootstrap directory")
    return {"schema": 2, "stage": stage, "script": digest(script), "target": target,
            "root": str(root), "tools": str(tools), "requires": required,
            "sources": [sources[s] for s in stage["sources"]],
            "helpers": {str(helper.relative_to(HERE)): digest(helper) for helper in helpers},
            "engine": engine, "seeds": seeds}


def check_prerequisites(name: str, stages: dict, stamps: Path, target: str,
                        root: Path, tools: Path, sources: dict, engine: dict, seeds: dict,
                        checked: dict) -> dict:
    path = stamps / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"Missing completed prerequisite stage: {name}")
    record = read_stamp(path)
    # A preceding stage may have just been rebuilt during this invocation.
    if checked.get(name) == record["fingerprint"]:
        return record
    check_stamp(record, name)
    required = []
    for predecessor in stages[name]["requires"]:
        previous = check_prerequisites(predecessor, stages, stamps, target, root, tools,
                                       sources, engine, seeds, checked)
        required.append({"stage": predecessor, "fingerprint": previous["fingerprint"],
                         "artifact_fingerprint": previous["artifact_fingerprint"]})
    expected = stage_identity(stages[name], target, root, tools, required, sources, engine, seeds)
    if record["fingerprint"] != fingerprint(expected):
        raise ValueError(f"Prerequisite {name} inputs changed; run the full bootstrap to update its closure")
    checked[name] = record["fingerprint"]
    return record


def project_output(raw: str, label: str, create: bool = True) -> Path:
    result = Path(raw).expanduser().resolve()
    if not result.is_relative_to(PROJECT) or result == PROJECT:
        raise ValueError(f"{label} must be a descendant of {PROJECT}")
    if create:
        result.mkdir(parents=True, exist_ok=True)
    elif not result.is_dir():
        raise ValueError(f"Missing {label} directory: {result}")
    return result


def extract(archive: Path, destination: Path, expected_hash: str) -> Path:
    marker = destination / ".source-sha256"
    if marker.is_file() and marker.read_text().strip() == expected_hash:
        roots = [p for p in destination.iterdir() if p.is_dir()]
        if len(roots) == 1:
            return roots[0]
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(archive) as stream:
        stream.extractall(destination, filter="data")
    roots = [p for p in destination.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise ValueError(f"Expected one source root in {archive.name}")
    marker.write_text(expected_hash + "\n")
    return roots[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", action="append", help="Select a stage; repeat for multiple stages (default: all)")
    parser.add_argument("--sources", required=True, help="Flat cache of locked source archives")
    parser.add_argument("--work", default=str(PROJECT / "out/bootstrap/work"))
    parser.add_argument("--root", default=str(PROJECT / "out/bootstrap/root"))
    parser.add_argument("--tools", default=str(PROJECT / "out/bootstrap/root/tools"))
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="Read-only verification of selected stages, their current inputs, and transitive artifact receipts; never compile or update stamps")
    parser.add_argument("--upgrade-stamps", action="store_true",
                        help="Explicitly migrate legacy stamps to current-baseline receipts and rerun loader probes; does not rebuild historical stages")
    args = parser.parse_args()
    selected_args = args.stage or ["all"]
    if not 1 <= args.jobs <= 128:
        parser.error("--jobs must be between 1 and 128")
    if os.geteuid() == 0 and not args.check:
        parser.error("Run unprivileged: bootstrap installs only into project directories")
    if os.uname().machine != "x86_64":
        parser.error("This initial bootstrap supports x86_64 build machines")
    if args.check and (args.upgrade_stamps or args.force):
        parser.error("--check cannot be combined with --upgrade-stamps or --force")
    if args.upgrade_stamps and (selected_args != ["all"] or args.force):
        parser.error("--upgrade-stamps requires --stage all and cannot be combined with --force")
    try:
        work = project_output(args.work, "work", create=not args.check)
        root = project_output(args.root, "root", create=not args.check)
        tools = project_output(args.tools, "tools", create=not args.check)
        if len({work, root, tools}) != 3:
            raise ValueError("work, root and tools must be distinct")
        sources_dir = Path(args.sources).expanduser().resolve(strict=True)
        manifest = tomllib.loads((HERE / "sources.lock.toml").read_text())
        plan = tomllib.loads((HERE / "stages.toml").read_text())
        sources = {item["name"]: item for item in manifest["sources"]}
        stages = {item["name"]: item for item in plan["stages"]}
        if "all" in selected_args and selected_args != ["all"]:
            raise ValueError("Select all stages or specific stages, not both")
        for stage_name in selected_args:
            if stage_name != "all" and stage_name not in stages:
                raise ValueError(f"Unknown stage: {stage_name}; choices: {', '.join(stages)}")
        selected = list(stages) if selected_args == ["all"] else list(dict.fromkeys(selected_args))
        target = plan["target"]
        environment = {
            "PATH": f"{tools}/native/bin:{tools}/bin:/usr/bin:/bin",
            "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
            "SOURCE_DATE_EPOCH": "1756684800",
            "CD_SYSROOT": str(root), "CD_TOOLS": str(tools),
            "CD_JOBS": str(args.jobs), "CD_TARGET": target,
        }
        stamps = work / "stamps"
        if not args.check:
            stamps.mkdir(exist_ok=True)
        elif not stamps.is_dir():
            raise ValueError(f"Missing completion stamps: {stamps}")
        lock = (work / ".bootstrap.lock").open("rb" if args.check else "a+")
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if args.check else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another bootstrap operation holds the project lock")
        original_records = {name: read_stamp(stamps / f"{name}.json")
                            for name in stages if (stamps / f"{name}.json").is_file()}
        seeds = seed_identity()
        engine = {"runner": digest(Path(__file__)), "integrity": digest(HERE / "integrity.py")}
        checked_dependencies = {}
        if args.check:
            report = {}
            for name in selected:
                record = check_prerequisites(name, stages, stamps, target, root, tools,
                                             sources, engine, seeds, checked_dependencies)
                report[name] = {"fingerprint": record["fingerprint"],
                                "artifact_fingerprint": record["artifact_fingerprint"],
                                "origin": record["origin"]}
            # Source pins in receipts must still describe the verified cache;
            # checking dependencies must not leave modified archives unnoticed.
            for name in checked_dependencies:
                source_checks(stages[name]["sources"], sources, sources_dir)
            print(json.dumps({"result": "CUSTOM_BOOTSTRAP_CHECK_OK", "selected": report,
                              "verified_closure": list(checked_dependencies), "read_only": True}, indent=2), flush=True)
            return 0
        if args.upgrade_stamps:
            selected = [name for name in selected if name in original_records]
            print("Recording explicit current-baseline receipts; historical seed/output integrity is not retroactively established", flush=True)
        mapping = {"tools": str(tools), "sysroot": str(root), "target": target}
        for name in selected:
            stage = stages[name]
            script = (HERE / stage["script"]).resolve(strict=True)
            if not script.is_relative_to(HERE):
                raise ValueError("Stage script escapes bootstrap directory")
            helpers = [(HERE / raw).resolve(strict=True) for raw in stage.get("helpers", [])]
            if any(not helper.is_relative_to(HERE) for helper in helpers):
                raise ValueError("Stage helper escapes bootstrap directory")
            required = []
            for predecessor in stage["requires"]:
                stamp_path = stamps / f"{predecessor}.json"
                if not stamp_path.is_file():
                    raise ValueError(f"{name} requires completed stage {predecessor}")
                previous = check_prerequisites(predecessor, stages, stamps, target, root, tools,
                                                sources, engine, seeds, checked_dependencies)
                required.append({"stage": predecessor, "fingerprint": previous["fingerprint"],
                                 "artifact_fingerprint": previous["artifact_fingerprint"]})
            identity = stage_identity(stage, target, root, tools, required, sources, engine, seeds)
            stage_fingerprint = fingerprint(identity)
            stamp = stamps / f"{name}.json"
            outputs = [Path(p.format(**mapping)) for p in stage["outputs"]]
            source_checks(stage["sources"], sources, sources_dir)
            if args.upgrade_stamps:
                old = original_records[name]
                if old.get("schema") == 2:
                    check_stamp(old, name)
                    if old["fingerprint"] == stage_fingerprint:
                        print(f"{name}: verified existing receipts", flush=True)
                        continue
                    unchanged = [key for key in identity if key not in ["engine", "requires"]]
                    if old.get("origin") != "legacy-current-baseline" or any(old["identity"][key] != identity[key] for key in unchanged):
                        raise ValueError(f"{name} schema-2 source/build inputs changed; rebuild rather than adopting changed inputs")
                    if [row["artifact_fingerprint"] for row in old["identity"]["requires"]] != [row["artifact_fingerprint"] for row in required]:
                        raise ValueError(f"{name} prerequisite artifacts changed; rebuild rather than refreshing its baseline")
                    # Receipt-only runner revisions may refresh an explicitly
                    # adopted baseline; they never relabel a build-with-receipts.
                    if name in ["validate", "gcc-pass2"]:
                        probe = script if name == "validate" else HERE / "stages/validate-runtime.py"
                        probe_env = environment | {"CD_WORK_DIR": str(work / name / "build")}
                        subprocess.run(["python3", str(probe)], cwd=work / name / "build", env=probe_env, check=True)
                    artifacts = stage_receipt(stage, [Path(path) for path in old["artifact_roots"]], root, tools, target)
                    if artifacts != old["artifacts"]:
                        raise ValueError(f"{name} refreshed validation changed its artifacts; inspect before refreshing")
                    refreshed = old | {"fingerprint": stage_fingerprint, "identity": identity,
                                       "previous_baseline_fingerprints": old.get("previous_baseline_fingerprints", []) + [old["fingerprint"]]}
                    write_stamp(stamp, refreshed)
                    print(f"{name}: explicit baseline runner receipt refreshed", flush=True)
                    continue
                legacy_stage = {key: value for key, value in stage.items() if key != "helpers"}
                legacy = {"stage": legacy_stage, "script": digest(script), "target": target,
                          "root": str(root), "tools": str(tools),
                          "requires": [original_records[p].get("previous_fingerprint", original_records[p]["fingerprint"])
                                       for p in stage["requires"]],
                          "sources": [sources[s] for s in stage["sources"]]}
                legacy_fingerprint = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
                if old["fingerprint"] != legacy_fingerprint:
                    raise ValueError(f"{name} legacy inputs changed; rebuild this stage before recording a baseline")
                if not all(path.exists() for path in outputs):
                    raise ValueError(f"{name} legacy output is missing; rebuild before recording a baseline")
                if name in ["validate", "gcc-pass2"]:
                    probe = script if name == "validate" else HERE / "stages/validate-runtime.py"
                    probe_env = environment | {"CD_WORK_DIR": str(work / name / "build")}
                    subprocess.run(["python3", str(probe)], cwd=work / name / "build", env=probe_env, check=True)
                artifact_roots = stage_artifacts(name, outputs, root, tools, target)
                artifacts = stage_receipt(stage, artifact_roots, root, tools, target)
                record = old | {"schema": 2, "fingerprint": stage_fingerprint, "identity": identity,
                        "artifact_roots": [str(path) for path in artifact_roots], "artifacts": artifacts,
                        "artifact_fingerprint": fingerprint(artifacts),
                        "origin": "legacy-current-baseline", "previous_fingerprint": old["fingerprint"],
                        "baseline_notice": "Original builds lacked these receipts. This records current artifacts/current seeds; loader probes were rerun."}
                write_stamp(stamp, record)
                print(f"{name}: current baseline recorded", flush=True)
                continue
            if not args.force and stamp.is_file():
                old = read_stamp(stamp)
                check_stamp(old, name)
                if old["fingerprint"] == stage_fingerprint:
                    print(f"{name}: up to date", flush=True)
                    continue
            # Changed inputs must not inherit objects or modified sources from a
            # different build identity. Explicit migration handles old builds.
            if (work / name).exists():
                shutil.rmtree(work / name)
            prepared = work / name / "sources"
            prepared.mkdir(parents=True, exist_ok=True)
            for source_name in stage["sources"]:
                item = sources[source_name]
                archive = sources_dir / item["filename"]
                extract(archive, prepared / source_name, item["sha256"])
            stage_work = work / name / "build"
            stage_work.mkdir(exist_ok=True)
            log = work / name / "build.log"
            env = environment | {"CD_SOURCES_DIR": str(prepared), "CD_WORK_DIR": str(stage_work)}
            if stage["sources"]:
                env["CD_SOURCE_DIR"] = str(next((prepared / stage["sources"][0]).glob("*/")))
            print(f"{name}: building; log {log}", flush=True)
            started = time.monotonic()
            with log.open("w") as output:
                command = [stage.get("interpreter", "/usr/bin/bash"), str(script)]
                result = subprocess.run(command, cwd=stage_work,
                                        env=env, stdout=output, stderr=subprocess.STDOUT)
            if result.returncode or not all(p.exists() for p in outputs):
                print("\n".join(log.read_text(errors="replace").splitlines()[-60:]), flush=True)
                raise ValueError(f"Stage {name} failed; exit {result.returncode}; log {log}")
            artifact_roots = stage_artifacts(name, outputs, root, tools, target)
            artifacts = stage_receipt(stage, artifact_roots, root, tools, target)
            record = {"schema": 2, "stage": name, "fingerprint": stage_fingerprint, "identity": identity,
                      "seconds": round(time.monotonic() - started, 2),
                      "outputs": [str(p) for p in outputs], "log": str(log), "origin": "built-with-receipts",
                      "artifact_roots": [str(path) for path in artifact_roots], "artifacts": artifacts,
                      "artifact_fingerprint": fingerprint(artifacts)}
            write_stamp(stamp, record)
            print(f"{name}: complete in {record['seconds']}s", flush=True)
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"bootstrap: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
