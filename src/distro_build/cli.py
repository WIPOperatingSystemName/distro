"""One executable interface for development and CI."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
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


def build_and_run(project: Path, args: argparse.Namespace) -> int:
    """Build through the existing CLI stages, then boot the composed image."""
    from . import apps, vm, vm_session
    from .compose import console_image, runtime_packages
    from .runner import run

    desktop = args.profile == "desktop-use"
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
    step("doctor")
    step("validate")
    step("fetch", *packages, "--bootstrap", *(["--offline"] if args.offline else []))
    step("bootstrap", "--jobs", jobs)
    step("bootstrap", "--check")
    step("native-toolkit", "--jobs", jobs, *([] if args.offline else ["--fetch"]))
    step("build", *packages, "--jobs", jobs)
    step("loader", *sources, *online)
    if desktop:
        run([sys.executable, str(project / "tools/compose-desktop-sdk.py")], project,
            os.environ.copy(), project / "out/logs/desktop-sdk.log")
        sdk = json.loads((project / "out/sdk/current.json").read_text())
        step("apps", "prepare", *sources)
        readiness = apps.preflight(project, Path(sdk["sysroot"]))
        if not readiness["ready_to_build"]:
            emit(readiness)
            raise BuildError("desktop SDK preflight failed; see missing inputs above")
        step("apps", "build", "--sysroot", sdk["sysroot"], "--jobs", jobs, *online)

    emit({"event": "stage", "command": ["image", "--profile", args.profile]})
    report = console_image(project, profile_name=args.profile)
    emit({"event": "image", "path": report["image"]["path"], "sha256": report["image"]["sha256"]})
    image = Path(report["image"]["path"])
    if desktop:
        # Filesystem timestamps can change image bytes without changing the
        # build. Reuse that build's disk; isolate a different build's disk.
        name = args.name or f"test-{report['identity'][:16]}"
        emit(vm_session.start(project, image, name=name))
        return 0
    output = args.output or project / "out/verification" / f"run-{args.profile}"
    if not output.is_absolute():
        output = project / output
    result = vm.run(image, output, timeout=args.timeout, headless=args.headless,
                    interactive=not args.headless, project=project,
                    expect="CUSTOM_SYSTEMD_RUNTIME_OK" if args.profile == "systemd"
                    else "CUSTOM_DISTRO_PERSISTENT_ROOT_OK")
    emit(result)
    return 0 if result["success"] else 1


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Build Custom Distro from source")
    command.add_argument("--project", type=Path, default=project_root())
    actions = command.add_subparsers(dest="command", required=True)
    action = actions.add_parser("run", help="Build the OS image from source and run it in QEMU")
    action.add_argument("--profile", choices=("desktop-use", "systemd", "console"), default="desktop-use")
    action.add_argument("--jobs", type=positive_integer, default=4)
    action.add_argument("--offline", action="store_true", help="Require verified cached sources and Cargo inputs")
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
    action.add_argument("--profile", choices=("console", "systemd", "desktop", "desktop-use"), default="console")
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
    return command


def main(argv: list[str] | None = None, *, quiet: bool = False) -> int:
    report = (lambda value: None) if quiet else emit
    args = parser().parse_args(argv)
    project = args.project.resolve()
    try:
        if args.command == "run":
            if args.output and args.profile == "desktop-use":
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
                from .runner import build_package
                artifacts = {}
                for recipe in ordered:
                    closure = plan(recipes, list(recipe.dependencies.get("target", ())), include_runtime=False)
                    if {"systemd", "libudev"} <= {dependency.name for dependency in closure}:
                        # The selected systemd package supplies libudev and conflicts
                        # with the standalone eudev package. Install one API provider.
                        closure = [dependency for dependency in closure if dependency.name != "libudev"]
                    dependencies = [Path(artifacts[dependency.name]["path"]) for dependency in closure]
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
            if args.use:
                if args.desktop_input or args.interactive or args.output:
                    raise BuildError("--use has its own persistent VM directory; omit --desktop-input, --interactive and --output")
                from .vm_session import start
                report(start(project, args.image or project / "out/images/custom-distro-desktop-use.img", name=args.name))
                return 0
            from .vm import run
            output = args.output or project / "out/vm"
            if not output.is_absolute():
                output = project / output
            result = run(args.image or project / "out/images/custom-distro.img", output, timeout=args.timeout, headless=not (args.window or args.interactive), expect=args.expect, project=project,
                         desktop_input=args.desktop_input, interactive=args.interactive)
            report(result)
            return 0 if result["success"] else 1
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
