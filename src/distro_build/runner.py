"""Trusted development build execution, staging and artifact provenance."""
from __future__ import annotations

import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from .model import BuildError, Recipe
from .sources import extract, fetch, sha256


def run(arguments: list[str], cwd: Path, env: dict[str, str], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"event": "command", "argv": arguments, "cwd": str(cwd), "log": str(log)}), flush=True)
    with log.open("ab") as output:
        output.write(("\n$ " + repr(arguments) + "\n").encode())
        started = time.monotonic()
        with subprocess.Popen(arguments, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, umask=0o022) as process:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print(json.dumps({"event": "building", "elapsed_seconds": round(time.monotonic() - started), "log": str(log)}), flush=True)
        if code:
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 6000))
                tail = stream.read().decode(errors="replace")
            raise BuildError(f"command exited {code}; log: {log}\n{tail}")


def seed_report() -> dict:
    report = {"kind": "declared-host-seed", "tools": {}}
    for command in ("python3", "gcc", "g++", "ld", "make", "bash", "tar", "xz", "perl", "meson", "ninja", "pkg-config"):
        executable = shutil.which(command)
        if not executable:
            raise BuildError(f"missing host seed tool: {command}")
        version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
        report["tools"][command] = {"path": executable, "sha256": sha256(Path(executable)), "version": version.stdout.splitlines()[0] if version.stdout else version.stderr.splitlines()[0]}
    for command in ("cmake", "autoconf", "automake", "libtoolize", "bison", "m4", "bc"):
        executable = shutil.which(command)
        report["tools"][command] = ({"available": True, "path": executable, "sha256": sha256(Path(executable))}
                                    if executable else {"available": False})
    # Python generators are executable native inputs too. Hash their source and
    # extension modules, rather than trusting a version string alone.
    report["python_modules"] = {}
    for name in ("jinja2", "markupsafe"):
        spec = importlib.util.find_spec(name)
        if spec is None:
            report["python_modules"][name] = {"available": False}
            continue
        roots = [Path(path) for path in (spec.submodule_search_locations or ())]
        files = sorted({path for root in roots for path in root.rglob("*")
                        if path.is_file() and (path.suffix == ".py" or path.suffix in {".so", ".pyd"})})
        if not roots and spec.origin and Path(spec.origin).is_file():
            files = [Path(spec.origin)]
        report["python_modules"][name] = {"available": True, "files": {
            str(path): sha256(path) for path in files}}
    return report


def build_identity(project: Path, recipe: Recipe, seed: dict, dependencies: list[Path], bootstrap: Path, *, seed_build: bool) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(seed, sort_keys=True).encode())
    digest.update(b"host-seed" if seed_build else b"target-bootstrap")
    excluded_modules = {"cli.py", "boot.py", "media.py", "vm.py", "apps.py", "compose.py", "source_bundle.py", "guest_test.py", "signed_test.py", "bootstrap_toolkit.py"}
    for tree in (recipe.path.parent, project / "src/distro_build"):
        for path in sorted(tree.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and (tree == recipe.path.parent or path.name not in excluded_modules):
                digest.update(str(path.relative_to(tree)).encode())
                digest.update(path.read_bytes())
    for source in recipe.sources:
        digest.update(source.sha256.encode())
    for name in ("run-stage.py", "integrity.py"):
        checker = project / "bootstrap" / name
        if checker.is_file():
            digest.update(name.encode())
            digest.update(checker.read_bytes())
    for dependency in dependencies:
        digest.update(sha256(dependency).encode())
    if bootstrap.exists():
        for path in sorted(bootstrap.rglob("*.json")):
            if path.is_file():
                digest.update(str(path.relative_to(bootstrap)).encode())
                receipt = json.loads(path.read_text())
                stable = {key: value for key, value in receipt.items() if key not in {"seconds", "elapsed_seconds", "log"}}
                digest.update(json.dumps(stable, sort_keys=True).encode())
    return digest.hexdigest()


def check_bootstrap(project: Path, tools: Path) -> None:
    """Reject stale or changed bootstrap tools before package/cache consumption."""
    output = project / "out"
    arguments = ["python3", "-B", str(project / "bootstrap/run-stage.py"),
                 "--sources", str(output / "sources/downloads"), "--work", str(output / "bootstrap/work"),
                 "--root", str(output / "bootstrap/root"), "--tools", str(tools), "--check",
                 "--stage", "validate", "--stage", "flex-native", "--stage", "elfutils-native"]
    if (tools / "pass2/.runtime-validated.json").is_file():
        arguments += ["--stage", "gcc-pass2"]
    if (output / "bootstrap/work/stamps/gperf-native.json").is_file():
        arguments += ["--stage", "gperf-native"]
    run(arguments, project, {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}, output / "logs/bootstrap-check.log")


def build_package(project: Path, recipe: Recipe, *, jobs: int, seed_build: bool = False,
                  dependencies: list[Path] | None = None, offline: bool = True) -> dict:
    """Serialize each package's generated work and installed artifact state."""
    locks = project / "out/state/locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / f"{recipe.name}.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _build_package(project, recipe, jobs=jobs, seed_build=seed_build, dependencies=dependencies, offline=offline)


def _build_package(project: Path, recipe: Recipe, *, jobs: int, seed_build: bool = False,
                   dependencies: list[Path] | None = None, offline: bool = True) -> dict:
    from .packaging import export_package

    output = project / "out"
    tools = output / "bootstrap/root/tools"
    sysroot = output / "bootstrap/root"
    target = "x86_64-custom-linux-gnu"
    compiler_tools = tools / "pass2" if (tools / "pass2/.runtime-validated.json").is_file() else tools
    compiler = compiler_tools / "bin" / f"{target}-gcc"
    if not seed_build and not compiler.exists():
        raise BuildError("target compiler missing; run bootstrap first, or explicitly use --seed for development-only artifacts")
    if not seed_build:
        check_bootstrap(project, tools)
    seed = seed_report()
    identity = build_identity(project, recipe, seed, dependencies or [], output / "bootstrap/work/stamps", seed_build=seed_build)
    state = output / "state/packages" / f"{recipe.name}.json"
    if state.exists():
        prior = json.loads(state.read_text())
        artifact = Path(prior.get("path", ""))
        if prior.get("build_identity") == identity and artifact.is_file() and sha256(artifact) == prior.get("sha256"):
            print(json.dumps({"event": "cache-hit", "package": recipe.name, "path": str(artifact)}), flush=True)
            return prior
    work = output / "work" / recipe.name / identity
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    inputs = work / "build-inputs"
    recipe_inputs = inputs / "packages" / recipe.name
    shutil.copytree(recipe.path.parent, recipe_inputs, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(project / "src/distro_build", inputs / "src/distro_build", ignore=shutil.ignore_patterns("__pycache__"))
    source_trees = work / "sources"
    prepared = {}
    for source in recipe.sources:
        archive = fetch(source, output / "sources/downloads", offline=offline)
        if source.format == "file":
            directory = source_trees / source.name
            directory.mkdir(parents=True)
            shutil.copy2(archive, directory / source.filename)
            prepared[source.name] = directory
        else:
            prepared[source.name] = extract(archive, source_trees / source.name)
    primary = prepared[recipe.sources[0].name]
    stage = work / "stage"
    stage.mkdir()
    stage.chmod(0o755)
    if dependencies and not seed_build:
        from .packaging import PacmanToolkit
        private_sysroot = work / "sysroot"
        toolkit = PacmanToolkit(output / "native-toolkit/prefix")
        toolkit.install(private_sysroot, dependencies, bootstrap=True,
                        expected_hashes={path.name: sha256(path) for path in dependencies},
                        compile_sysroot=True)
        shutil.copytree(sysroot / "usr/include", private_sysroot / "usr/include", symlinks=True, dirs_exist_ok=True)
        shutil.copy2(sysroot / ".bootstrap-validated.json", private_sysroot / ".bootstrap-validated.json")
        sysroot = private_sysroot
    paths = {"recipe": str(recipe_inputs), "source": str(primary), "sources": str(source_trees),
             "work": str(work), "stage": str(stage), "sysroot": str(sysroot), "tools": str(tools), "jobs": str(jobs)}
    env = {"PATH": f"{tools / 'native/bin'}:{compiler_tools / 'bin'}:{tools / 'bin'}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC",
           "SOURCE_DATE_EPOCH": "1756684800", "CD_SOURCE_DIR": str(primary), "CD_SOURCES_DIR": str(source_trees),
           "CD_WORK_DIR": str(work), "CD_STAGE_DIR": str(stage), "CD_SYSROOT": str(sysroot),
           "CD_TOOLS": str(tools), "CD_JOBS": str(jobs), "CD_TARGET": target,
           "CD_BOOTSTRAP_ROOT": str(output / "bootstrap/root"),
           "CD_COMPILER_TOOLS": str(compiler_tools),
           "CD_CXX": "/usr/bin/g++" if seed_build else str(compiler_tools / "bin" / f"{target}-g++"),
           "CD_CC": "/usr/bin/gcc" if seed_build else str(compiler), "CD_BUILD_MODE": "seed" if seed_build else "target"}
    for key, value in recipe.build.get("environment", {}).items():
        if not isinstance(key, str) or not isinstance(value, str) or key in {"HOME", "CODEX_HOME"}:
            raise BuildError(f"{recipe.path}: invalid build environment override")
        env[key] = value.format_map(paths)
    log = output / "logs/packages" / f"{recipe.name}-{identity[:12]}.log"
    adapter = recipe.build["adapter"]
    if adapter == "script":
        commands = [[argument.format_map(paths) for argument in command] for command in recipe.build["commands"]]
    elif adapter == "autotools":
        commands = [[str(primary / "configure"), "--prefix=/usr", *recipe.build.get("configure", [])],
                    ["make", f"-j{jobs}"], ["make", f"DESTDIR={stage}", "install"]]
        env["CC"] = env["CD_CC"]
    else:
        commands = [["make", f"-j{jobs}", *recipe.build.get("arguments", [])],
                    ["make", f"DESTDIR={stage}", "PREFIX=/usr", "install"]]
        env["CC"] = env["CD_CC"]
    for command in commands:
        run(command, primary, env, log)
    if not any(path.is_file() or path.is_symlink() for path in stage.rglob("*")):
        raise BuildError(f"{recipe.name}: empty staged package")
    metadata = {**recipe.package, "depends": list(recipe.dependencies.get("runtime", ())),
                "licenses": [recipe.package["license"]]}
    provenance = {"build_identity": identity, "seed": seed, "build_mode": env["CD_BUILD_MODE"],
                  "sources": [source.__dict__ for source in recipe.sources], "log": str(log),
                  "supplied_compiler": {"path": env["CD_CC"], "sha256": sha256(Path(env["CD_CC"]))},
                  "frozen_inputs": str(inputs)}
    artifact = export_package(stage, output / "packages" / recipe.name / identity, metadata, source_date_epoch=int(env["SOURCE_DATE_EPOCH"]), provenance=provenance)
    result = {"package": recipe.name, "build_identity": identity, "path": str(artifact.path),
              "sha256": sha256(artifact.path), "stage": str(stage), "provenance": provenance}
    state.parent.mkdir(parents=True, exist_ok=True)
    temporary = state.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(temporary, state)
    print(json.dumps({"event": "built", "package": recipe.name, "path": result["path"]}), flush=True)
    return result
