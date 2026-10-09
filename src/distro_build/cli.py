"""One executable interface for development and CI."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig
import time
import tomllib

from .graph import plan, affected
from .model import BuildError, Source, catalog
from .sources import fetch_all
from .packaging import PackageError, ToolkitError


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, default=str), flush=True)


def bootstrap_sources(project: Path) -> list[Source]:
    lock = project / "bootstrap/sources.lock.toml"
    if not lock.exists():
        raise BuildError(f"bootstrap source lock not available: {lock}")
    data = tomllib.loads(lock.read_text())
    return [Source.parse(source, str(lock)) for source in data["sources"]]


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def vm_timeout(value: str) -> int:
    number = positive_integer(value)
    if number > 3600:
        raise argparse.ArgumentTypeError("must be between 1 and 3600 seconds")
    return number


class DevelopmentCache:
    """Reuse completed local stages after checking input and output contents.

    Only completed actions are recorded. Inventories cover file hashes,
    replacements, modes, symlink targets and directory membership.
    The caller serializes preparation with run.lock, releasing it before QEMU.
    """

    def __init__(self, project: Path, profile: str, *, verify: bool = False):
        self.directory = project / "out/state/run" / profile
        self.directory.mkdir(parents=True, exist_ok=True)
        self.verify = verify

    @staticmethod
    def inventory(paths: list[Path]) -> str:
        digest = hashlib.sha256()
        excluded = {".git", "__pycache__", ".cache", "target", "vendor", "out"}
        visited = set()

        def visit(path: Path) -> None:
            if path in visited:
                return
            visited.add(path)
            try:
                info = path.lstat()
            except FileNotFoundError:
                digest.update(json.dumps([str(path), "missing"]).encode())
                return
            row = [str(path), info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]
            if path.is_symlink():
                row.append(os.readlink(path))
            elif not path.is_dir():
                row.extend([info.st_size, info.st_mtime_ns, info.st_ctime_ns])
                if path.is_file():
                    from .sources import sha256
                    row.append(sha256(path))
            digest.update(json.dumps(row).encode())
            if path.is_symlink():
                visit(path.resolve())
            elif path.is_dir():
                for child in sorted(path.iterdir()):
                    if child.name not in excluded and child.suffix != ".pyc":
                        visit(child)

        for path in sorted(set(paths)):
            visit(path)
        return digest.hexdigest()

    def run(self, name: str, inputs, outputs, action, *, options=None):
        started = time.monotonic()
        # Hash environment values without writing them (or credentials) to disk.
        environment = {key: os.environ.get(key) for key in (
            "PATH", "CC", "CXX", "AR", "AS", "LD", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS",
            "PKG_CONFIG_PATH", "PKG_CONFIG_SYSROOT_DIR", "PKG_CONFIG_LIBDIR", "RUSTUP_HOME", "CARGO_HOME",
            "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "SOURCE_DATE_EPOCH")}
        identity = hashlib.sha256(json.dumps({"schema": 1, "inputs": self.inventory(inputs()),
            "options": options, "environment": environment}, sort_keys=True).encode()).hexdigest()
        record = self.directory / f"{name}.json"
        prior = None
        if record.is_file():
            try:
                prior = json.loads(record.read_text())
            except (ValueError, OSError):
                pass
        if not self.verify and isinstance(prior, dict) and prior.get("identity") == identity:
            paths = outputs(prior.get("result"))
            if paths and all(path.exists() for path in paths) and prior.get("outputs") == self.inventory(paths):
                emit({"event": "stage-cached", "stage": name, "seconds": round(time.monotonic() - started, 2)})
                return prior.get("result")
        # A failed or interrupted action must not leave an older success reusable.
        record.unlink(missing_ok=True)
        emit({"event": "stage-start", "stage": name})
        result = action()
        current = hashlib.sha256(json.dumps({"schema": 1, "inputs": self.inventory(inputs()),
            "options": options, "environment": environment}, sort_keys=True).encode()).hexdigest()
        if current != identity:
            raise BuildError(f"{name} inputs changed during preparation; rerun before testing")
        paths = outputs(result)
        if paths and all(path.exists() for path in paths):
            temporary = record.with_suffix(".next")
            temporary.write_text(json.dumps({"identity": identity, "outputs": self.inventory(paths),
                                             "result": result}, indent=2) + "\n")
            temporary.replace(record)
        emit({"event": "stage-done", "stage": name, "seconds": round(time.monotonic() - started, 2)})
        return result


def package_outputs(project: Path, names: list[str], *, applications: bool = False) -> list[Path]:
    paths = []
    for name in names:
        state = project / "out/state" / ("apps" if applications else "packages") / f"{name}.json"
        paths.append(state)
        if state.is_file():
            paths.append(Path(json.loads(state.read_text())["path"]))
    return paths


def cached_package(project: Path, recipe, seed: dict, dependencies: list[Path], *, seed_build: bool = False):
    from .runner import build_identity
    from .sources import sha256
    locks = project / "out/state/locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / f"{recipe.name}.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = project / "out/state/packages" / f"{recipe.name}.json"
        if not state.is_file():
            return None
        prior = json.loads(state.read_text())
        artifact = Path(prior.get("path", ""))
        identity = build_identity(project, recipe, seed, dependencies, project / "out/bootstrap/work/stamps", seed_build=seed_build)
        if prior.get("build_identity") != identity or not artifact.is_file() or sha256(artifact) != prior.get("sha256"):
            return None
        return prior


def package_dependencies(recipes: dict, recipe, artifacts: dict) -> list[Path]:
    closure = plan(recipes, list(recipe.dependencies.get("target", ())), include_runtime=False)
    if {"systemd", "libudev"} <= {dependency.name for dependency in closure}:
        # systemd supplies the API and conflicts with the standalone eudev package.
        closure = [dependency for dependency in closure if dependency.name != "libudev"]
    return [Path(artifacts[dependency.name]["path"]) for dependency in closure]


def reuse_package_set(project: Path, recipes: dict, ordered: list) -> bool:
    """Check cache identities after the caller has verified the whole bootstrap.

    This uses the package runner's identity and artifact checks, sharing the
    already completed bootstrap audit rather than repeating it per package.
    A miss falls back to the normal package builder.
    """
    from .runner import seed_report
    if not ordered:
        return False
    seed = seed_report(project)
    artifacts = {}
    for recipe in ordered:
        prior = cached_package(project, recipe, seed, package_dependencies(recipes, recipe, artifacts))
        if prior is None:
            return False
        artifacts[recipe.name] = prior
    emit({"event": "package-set-cached", "packages": len(artifacts)})
    return True


def build_and_run(project: Path, args: argparse.Namespace) -> int:
    """Build through the existing CLI stages, then boot the composed image."""
    from . import apps, vm, vm_session
    from .compose import console_image, runtime_packages
    from .runner import run

    started = time.monotonic()
    desktop = args.profile in {"desktop-use", "desktop-dev"}
    if desktop and args.headless:
        raise BuildError("--headless requires --profile console or --profile systemd")
    if args.name and not desktop:
        raise BuildError("--name requires --profile desktop-use")
    vm.discover_runtime(project)
    source_root = (args.source_root or project / "sources").resolve()
    for name in ("telorgon", "telorgon-bootloader"):
        if not (source_root / name / "Cargo.toml").is_file():
            raise BuildError(f"missing {name} sources in {source_root}; initialize the pinned submodules "
                             "with git submodule update --init --recursive")

    def step(*arguments: str) -> None:
        emit({"event": "stage", "command": list(arguments)})
        if main(["--project", str(project), *arguments], quiet=True):
            raise BuildError(f"{arguments[0]} failed; build-and-run stopped")

    jobs = str(args.jobs)
    profile = tomllib.loads((project / f"profiles/{args.profile}.toml").read_text())
    packages = [] if desktop else runtime_packages(project, profile["packages"],
        providers={"libudev": "systemd"} if args.profile == "systemd" else {})
    online = [] if args.offline else ["--online"]
    sources = ["--source-root", str(source_root)]
    cache = DevelopmentCache(project, args.profile, verify=args.verify)
    output = project / "out"
    engine = [project / "src/distro_build", project / "build.py"]
    recipes = catalog(project)
    ordered = plan(recipes, packages or sorted(recipes)) if recipes else []
    names = [recipe.name for recipe in ordered]
    bootstrap = [output / "bootstrap/root", output / "bootstrap/work/stamps"]
    prefix = output / "native-toolkit/prefix"
    toolkit = [prefix / name for name in ("bin", "lib", "include", "share", "toolkit.json", ".development-dependencies.json")]
    seeds = [Path(path) for path in ("/usr/bin", "/usr/include", "/usr/lib/gcc",
        "/usr/lib/x86_64-linux-gnu", "/usr/lib/python3/dist-packages", sysconfig.get_path("stdlib"),
        sysconfig.get_path("purelib"), sys.executable)]
    seeds += [Path(path) for command in ("gcc", "g++", "ld", "make", "bash", "tar", "xz", "perl", "meson",
        "ninja", "pkg-config", "cmake", "autoconf", "automake", "libtoolize", "bison", "m4", "bc")
        if (path := shutil.which(command))]

    def essentials():
        step("doctor")
        step("validate")
        step("fetch", *packages, "--bootstrap", *(["--offline"] if args.offline else []))
        step("bootstrap", "--jobs", jobs)
        step("bootstrap", "--check")
        step("native-toolkit", "--jobs", jobs, *([] if args.offline else ["--fetch"]))
        if not reuse_package_set(project, recipes, ordered):
            step("build", *packages, "--jobs", jobs)

    def essential_outputs(_=None):
        return bootstrap + toolkit + [output / "sources/downloads", output / "native-toolkit/sources"] + package_outputs(project, names)

    loader = [output / "loader/cargo-target/x86_64-unknown-uefi/release/boot-efi.efi",
              output / "loader/loader-sources.json"]
    from .boot import EFI_TOOLCHAIN, _git_metadata
    rust = Path(os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup"))) / "toolchains" / f"{EFI_TOOLCHAIN}-x86_64-unknown-linux-gnu"
    rust_seeds = [rust] + [Path(path) for command in ("cargo", "rustup") if (path := shutil.which(command))]
    rust_seeds += [Path(path) for name in ("RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER")
                   if (value := os.environ.get(name)) and (path := shutil.which(value))]

    # Serialize preparation, but release the lock before an interactive VM opens.
    with (output / "state/run.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        cache.run("essentials", lambda: engine + [project / "packages", project / "bootstrap", project / "profiles" / f"{args.profile}.toml"] + seeds,
                  essential_outputs, essentials)
        cache.run("loader", lambda: engine + [source_root / name for name in ("telorgon", "telorgon-bootloader")] + rust_seeds,
                  lambda _: loader, lambda: step("loader", *sources, *online),
                  options={name: _git_metadata(source_root / name) for name in ("telorgon", "telorgon-bootloader")})
        if desktop:
            sdk_record = output / "sdk/current.json"

            def sdk_outputs(_):
                return [sdk_record, Path(json.loads(sdk_record.read_text())["sysroot"])] if sdk_record.is_file() else [sdk_record]

            cache.run("sdk", lambda: engine + [project / "tools/compose-desktop-sdk.py"] + essential_outputs(),
                      sdk_outputs, lambda: run([sys.executable, str(project / "tools/compose-desktop-sdk.py")], project,
                                              os.environ.copy(), output / "logs/desktop-sdk.log"))
            sdk = json.loads(sdk_record.read_text())

            def applications():
                step("apps", "prepare", *sources)
                readiness = apps.preflight(project, Path(sdk["sysroot"]))
                if not readiness["ready_to_build"]:
                    emit(readiness)
                    raise BuildError("desktop SDK preflight failed; see missing inputs above")
                step("apps", "build", "--sysroot", sdk["sysroot"], "--jobs", jobs, *online)

            cache.run("apps", lambda: engine + [project / "packages"] + rust_seeds + sdk_outputs(None)
                      + [source_root / name for name in apps.INPUTS],
                      lambda _: package_outputs(project, list(apps.catalog(project)), applications=True), applications,
                      options={name: _git_metadata(source_root / name) for name in apps.INPUTS})

        def image():
            emit({"event": "stage", "command": ["image", "--profile", args.profile]})
            return console_image(project, profile_name=args.profile)

        cache_inputs = lambda: engine + [project / "system", project / "profiles" / f"{args.profile}.toml"] + loader + package_outputs(project, names) + (
            package_outputs(project, list(apps.catalog(project)), applications=True) if desktop else [])
        report = cache.run("image", cache_inputs,
                          lambda result: [Path(result["image"]["path"])] if result else [], image)
    emit({"event": "image", "path": report["image"]["path"], "sha256": report["image"]["sha256"]})
    emit({"event": "ready-to-boot", "elapsed_seconds": round(time.monotonic() - started, 2)})
    image = Path(report["image"]["path"])
    if desktop:
        # Filesystem timestamps can change image bytes without changing the
        # build. Reuse that build's disk; isolate a different build's disk.
        name = args.name or f"test-{report['identity'][:16]}"
        options = {"development": True} if args.profile == "desktop-dev" else {}
        emit(vm_session.start(project, image, name=name, **options))
        return 0
    output = args.output or project / "out/verification" / f"run-{args.profile}"
    if not output.is_absolute():
        output = project / output
    result = vm.run(image, output, timeout=args.timeout, headless=args.headless,
                    interactive=not args.headless, project=project,
                    expect="CUSTOM_SYSTEMD_RUNTIME_OK" if args.profile == "systemd"
                    else "CUSTOM_DISTRO_PERSISTENT_ROOT_OK")
    emit(result)
    emit({"event": "run-complete", "success": result["success"],
          "elapsed_seconds": round(time.monotonic() - started, 2)})
    return 0 if result["success"] else 1


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Build Custom Distro from source")
    command.add_argument("--project", type=Path, default=project_root())
    actions = command.add_subparsers(dest="command", required=True)
    action = actions.add_parser("run", help="Build the OS image from source and run it in QEMU")
    action.add_argument("--profile", choices=("desktop-use", "desktop-dev", "systemd", "console"), default="desktop-use")
    action.add_argument("--jobs", type=positive_integer, default=4)
    action.add_argument("--offline", action="store_true", help="Require verified cached sources and Cargo inputs")
    action.add_argument("--verify", action="store_true", help="Run full build/input integrity checks instead of trusting local development stage receipts")
    action.add_argument("--source-root", type=Path, help="Source workspace override (default: sources/ submodules)")
    action.add_argument("--name", help="Resume a named desktop VM; default selects a VM for the newly built image")
    action.add_argument("--headless", action="store_true", help="Run a bounded console/systemd boot check without a window")
    action.add_argument("--timeout", type=vm_timeout, default=600, help="Console/systemd startup deadline (1–3600 seconds)")
    action.add_argument("--output", type=Path, help="Console/systemd boot evidence directory")
    actions.add_parser("doctor", help="Audit host seed tools and environment")
    actions.add_parser("validate", help="Validate package recipes and dependency graph")
    action = actions.add_parser("affected", help="List packages and dependents affected by recipe changes")
    action.add_argument("packages", nargs="+")
    action = actions.add_parser("plan", help="Print ordered package build plan")
    action.add_argument("packages", nargs="*")
    action = actions.add_parser("fetch", help="Download sources and verify pinned SHA256")
    action.add_argument("packages", nargs="*")
    action.add_argument("--bootstrap", action="store_true")
    action.add_argument("--offline", action="store_true")
    action.add_argument("--workers", type=int, default=4)
    action = actions.add_parser("bootstrap", help="Build the declared target toolchain")
    action.add_argument("--check", action="store_true", help="Verify completed bootstrap inputs/artifacts without compiling or changing stamps")
    action.add_argument("--upgrade-stamps", action="store_true", help="Explicitly record current-baseline receipts for legacy bootstrap artifacts")
    action.add_argument("--stage")
    action.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 12))
    action = actions.add_parser("native-toolkit", help="Build the private source-built assembly package manager")
    action.add_argument("--fetch", action="store_true")
    action.add_argument("--jobs", type=int, default=4)
    action = actions.add_parser("build", help="Build packages from verified cached sources")
    action.add_argument("packages", nargs="*")
    action.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 12))
    action.add_argument("--seed", action="store_true", help="Explicit development build using host compiler")
    action = actions.add_parser("loader", help="Build a recorded snapshot of the Telorgon EFI application")
    action.add_argument("--online", action="store_true")
    action.add_argument("--source-root", type=Path, help="Source workspace override (default: sources/ submodules)")
    action = actions.add_parser("source-bundle", help="Export or verify portable Telorgon source inputs for CI")
    action.add_argument("action", choices=("export", "import"))
    action.add_argument("archive", nargs="?", type=Path)
    action.add_argument("--sha256")
    action.add_argument("--source-root", type=Path, help="Source workspace override (default: sources/ submodules)")
    action = actions.add_parser("image", help="Compose a source-built system profile")
    action.add_argument("--profile", choices=("console", "systemd", "desktop", "desktop-use", "desktop-dev"), default="console")
    action.add_argument("--root", type=Path)
    action.add_argument("--test-upgrade", action="store_true")
    action.add_argument("--test-signed-upgrade", action="store_true", help="Build a disposable strict-signature positive/negative upgrade test image")
    action.add_argument("--test-pam-auth", action="store_true", help="Build a disposable systemd password-authentication fixture with locked defaults and temporary guest-only enrollment")
    action = actions.add_parser("vm", help="Boot the image in QEMU and collect serial evidence")
    action.add_argument("--image", type=Path)
    action.add_argument("--timeout", type=int, default=240)
    action.add_argument("--window", action="store_true")
    action.add_argument("--interactive", action="store_true", help="Open a QEMU window and keep it running after startup checks until you close it or press Ctrl+C")
    action.add_argument("--use", action="store_true", help="Use the normal desktop with a persistent VM disk, without boot assertions or test input")
    action.add_argument("--name", default="custom", help="Persistent VM name for --use")
    action.add_argument("--development", action="store_true", help="Expose the private guest-agent channel for a desktop-dev image; requires --use")
    action.add_argument("--audio", choices=("auto", "pulse", "none"), default="auto", help="Host sound backend for --use; auto detects WSLg/PulseAudio")
    action.add_argument("--usb-camera", help="Pass a USB webcam attached to Linux/WSL into --use, identified by bus:address")
    action.add_argument("--output", type=Path)
    action.add_argument("--expect", default="CUSTOM_DISTRO_PERSISTENT_ROOT_OK")
    action.add_argument("--desktop-input", action="store_true", help="Send Q only after the real Wayland qualifier reports focus and initial presentation")
    action = actions.add_parser("apps", help="Plan, snapshot, check or build Telorgon target applications")
    action.add_argument("action", choices=("plan", "prepare", "check", "build"))
    action.add_argument("names", nargs="*")
    action.add_argument("--sysroot", type=Path)
    action.add_argument("--tools", type=Path)
    action.add_argument("--online", action="store_true")
    action.add_argument("--source-root", type=Path, help="Source workspace override (default: sources/ submodules)")
    action.add_argument("--jobs", type=int, default=4)
    action = actions.add_parser("deploy", help="Build and install applications into a running named desktop-dev VM")
    action.add_argument("names", nargs="+")
    action.add_argument("--name", required=True, help="Running development VM name")
    action.add_argument("--source-root", type=Path)
    action.add_argument("--jobs", type=positive_integer, default=4)
    action.add_argument("--online", action="store_true", help="Allow fetching locked Cargo dependencies")
    action.add_argument("--build-profile", choices=("dev", "release"), default="dev")
    action.add_argument("--no-restart", action="store_true", help="Install packages without restarting the desktop; reopen updated apps manually")
    action.add_argument("--timeout", type=vm_timeout, default=120, help="Guest command deadline in seconds")
    return command


def main(argv: list[str] | None = None, *, quiet: bool = False) -> int:
    report = (lambda value: None) if quiet else emit
    args = parser().parse_args(argv)
    project = args.project.resolve()
    try:
        if args.command == "run":
            if args.output and args.profile in {"desktop-use", "desktop-dev"}:
                raise BuildError("--output requires --profile console or --profile systemd")
            return build_and_run(project, args)
        elif args.command == "doctor":
            from .runner import seed_report
            report(seed_report(project))
        elif args.command in {"validate", "plan", "affected", "fetch", "build"}:
            recipes = catalog(project)
            if not recipes and args.command != "fetch":
                raise BuildError("package catalog is empty")
            selected = getattr(args, "packages", None) or list(recipes)
            ordered = plan(recipes, selected)
            if args.command == "validate":
                from .apps import catalog as app_catalog
                report({"valid": True, "packages": [recipe.name for recipe in ordered], "applications": list(app_catalog(project))})
            elif args.command == "plan":
                report({"packages": [{"name": recipe.name, "dependencies": recipe.dependencies} for recipe in ordered]})
            elif args.command == "affected":
                unknown = set(args.packages) - recipes.keys()
                if unknown:
                    raise BuildError(f"unknown packages: {sorted(unknown)}")
                report({"affected": sorted(affected(recipes, set(args.packages)))})
            elif args.command == "fetch":
                sources = [source for recipe in ordered for source in recipe.sources]
                if args.bootstrap:
                    sources += bootstrap_sources(project)
                report({"verified_sources": fetch_all(sources, project / "out/sources/downloads", offline=args.offline, workers=args.workers)})
            else:
                from .runner import build_package, check_bootstrap, seed_report
                if not args.seed:
                    check_bootstrap(project, project / "out/bootstrap/root/tools")
                seed = seed_report(project)
                artifacts = {}
                for recipe in ordered:
                    dependencies = package_dependencies(recipes, recipe, artifacts)
                    prior = cached_package(project, recipe, seed, dependencies, seed_build=args.seed)
                    if prior is not None:
                        emit({"event": "cache-hit", "package": recipe.name, "path": prior["path"]})
                        artifacts[recipe.name] = prior
                    else:
                        artifacts[recipe.name] = build_package(project, recipe, jobs=args.jobs, seed_build=args.seed, dependencies=dependencies)
                report({"artifacts": artifacts})
        elif args.command == "bootstrap":
            from .runner import run
            output = project / "out"
            arguments = [sys.executable, str(project / "bootstrap/run-stage.py"),
                         "--sources", str(output / "sources/downloads"), "--work", str(output / "bootstrap/work"),
                         "--root", str(output / "bootstrap/root"), "--tools", str(output / "bootstrap/root/tools"),
                         "--jobs", str(args.jobs)]
            if args.stage:
                arguments += ["--stage", args.stage]
            if args.check:
                arguments += ["--check"]
            if args.upgrade_stamps:
                arguments += ["--upgrade-stamps"]
            run(arguments, project, {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}, output / "logs/bootstrap.log")
        elif args.command == "loader":
            from .boot import build_loader
            source_root = (args.source_root or project / "sources").resolve()
            report(build_loader(source_root / "telorgon-bootloader", source_root / "telorgon", project / "out/loader", offline=not args.online))
        elif args.command == "source-bundle":
            from .source_bundle import export_bundle, import_bundle
            if args.action == "export":
                report({key: value for key, value in export_bundle(project, args.source_root).items() if key != "manifest"})
            else:
                if not args.archive or not args.sha256:
                    raise BuildError("source-bundle import requires an archive and --sha256")
                report(import_bundle(project, args.archive, args.sha256))
        elif args.command == "native-toolkit":
            from .packaging.bootstrap_toolkit import build_development_toolkit
            toolkit = build_development_toolkit(project / "out/native-toolkit", fetch=args.fetch, jobs=args.jobs)
            report(toolkit.record)
        elif args.command == "image":
            from .compose import console_image
            report(console_image(project, args.root, test_upgrade=args.test_upgrade, test_signed_upgrade=args.test_signed_upgrade,
                               test_pam_auth=args.test_pam_auth, profile_name=args.profile))
        elif args.command == "vm":
            if not args.use and (args.audio != "auto" or args.usb_camera):
                raise BuildError("--audio and --usb-camera require --use")
            if args.development and not args.use:
                raise BuildError("--development requires --use")
            if args.use:
                if args.desktop_input or args.interactive or args.output:
                    raise BuildError("--use has its own persistent VM directory; omit --desktop-input, --interactive and --output")
                from .vm_session import start
                options = {"development": True} if args.development else {}
                if args.audio != "auto":
                    options["audio"] = args.audio
                if args.usb_camera:
                    options["usb_camera"] = args.usb_camera
                default_image = "custom-distro-desktop-dev.img" if args.development else "custom-distro-desktop-use.img"
                report(start(project, args.image or project / "out/images" / default_image, name=args.name, **options))
                return 0
            from .vm import run
            output = args.output or project / "out/vm"
            if not output.is_absolute():
                output = project / output
            result = run(args.image or project / "out/images/custom-distro.img", output, timeout=args.timeout, headless=not (args.window or args.interactive), expect=args.expect, project=project,
                         desktop_input=args.desktop_input, interactive=args.interactive)
            report(result)
            return 0 if result["success"] else 1
        elif args.command == "deploy":
            from .deploy import deploy
            report(deploy(project, args.names, name=args.name, source_root=args.source_root,
                          jobs=args.jobs, offline=not args.online, profile=args.build_profile,
                          restart=not args.no_restart, timeout=args.timeout, emit=emit))
        elif args.command == "apps":
            from . import apps
            if args.action == "plan":
                report(apps.plan(project, args.names))
            elif args.action == "prepare":
                report(apps.prepare(project, source_root=args.source_root))
            elif args.action == "check":
                report(apps.preflight(project, args.sysroot, tools=args.tools, names=args.names))
            else:
                names = args.names or list(apps.catalog(project))
                report({"artifacts": [apps.build_app(project, name, args.sysroot, args.tools, offline=not args.online, jobs=args.jobs) for name in names]})
        return 0
    except (BuildError, PackageError, ToolkitError, RuntimeError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"error": str(error), "command": args.command}), file=sys.stderr, flush=True)
        return 1
