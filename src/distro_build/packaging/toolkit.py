"""Build and use source-built pacman/libalpm without touching the host root."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Mapping, Sequence

from .archive import inspect_package


class ToolkitError(RuntimeError):
    pass


def _run(command: Sequence[str], *, env: Mapping[str, str], cwd: Path | None = None,
         log: Path | None = None) -> str:
    completed = subprocess.run(list(command), cwd=cwd, env=dict(env), text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as stream:
            stream.write("$ " + " ".join(command) + "\n" + completed.stdout)
    if completed.returncode:
        raise ToolkitError(f"command failed ({completed.returncode}): {command[0]}\n"
                           f"{completed.stdout[-6000:]}")
    return completed.stdout


def build_toolkit(source_dir: Path, prefix: Path, work_dir: Path,
                  env: Mapping[str, str] | None = None, *,
                  development: bool = False, jobs: int = 2) -> "PacmanToolkit":
    """Build official sources to prefix; all installation paths remain private.

    Caller supplies verified sources and an explicit dependency environment.
    Production mode requires curl/GPGME. Development mode is deliberately
    unsigned and cannot be used for release repository/network updates.
    """
    source_dir, prefix, work_dir = map(lambda x: Path(x).resolve(), (source_dir, prefix, work_dir))
    if prefix == Path("/") or prefix in {Path("/usr"), Path("/usr/local")}:
        raise ToolkitError("toolkit prefix must be a private build directory")
    if not (source_dir / "meson.build").is_file():
        raise ToolkitError("verified pacman source tree lacks meson.build")
    prefix.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C",
                   "XDG_CACHE_HOME": str(work_dir / "cache"),
                   "XDG_CONFIG_HOME": str(work_dir / "config"),
                   "TMPDIR": str(work_dir / "tmp"), "SOURCE_DATE_EPOCH": "1761955200"}
    environment.update(env or {})
    for key in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "TMPDIR"):
        Path(environment[key]).mkdir(parents=True, exist_ok=True)
    build_dir = work_dir / "pacman-build"
    options = ["--prefix=" + str(prefix), "--bindir=bin", "--libdir=lib",
               "--sysconfdir=" + str(prefix / "etc"),
               "--localstatedir=" + str(prefix / "var"), "-Ddoc=disabled", "-Di18n=false",
               "-Dcurl=" + ("disabled" if development else "enabled"),
               "-Dgpgme=" + ("disabled" if development else "enabled"),
               "-Dpkg-ext=.pkg.tar.xz", "-Dbuildtype=release"]
    log = work_dir / "toolkit-build.log"
    setup = ["meson", "setup"]
    if (build_dir / "meson-private/coredata.dat").exists():
        setup.append("--reconfigure")
    _run([*setup, str(build_dir), str(source_dir), *options], env=environment, log=log)
    _run(["meson", "compile", "-C", str(build_dir), "-j", str(jobs)], env=environment, log=log)
    # Optional upstream integration (e.g. bash-completion) can request absolute
    # system directories even when prefix is private. Capture EVERY install
    # with DESTDIR and copy only the prefix subtree; never write host paths.
    install_root = work_dir / "pacman-install-root"
    install_env = dict(environment)
    install_env["DESTDIR"] = str(install_root)
    _run(["meson", "install", "-C", str(build_dir), "--no-rebuild"], env=install_env, log=log)
    installed_prefix = install_root / prefix.relative_to("/")
    _run(["cp", "-a", str(installed_prefix) + "/.", str(prefix) + "/"], env=environment, log=log)
    compile_env = dict(environment)
    compile_env["PKG_CONFIG_PATH"] = str(prefix / "lib/pkgconfig") + os.pathsep + environment.get("PKG_CONFIG_PATH", "")
    flags = _run(["pkg-config", "--cflags", "--libs", "libalpm"], env=compile_env).split()
    helper_source = Path(__file__).with_name("native_install.c")
    # Only the standard private project toolkit may omit runtime dependencies
    # when assembling compile sysroots. Other toolkits keep a strict helper.
    compile_sysroot_base = None
    if development and prefix.name == "prefix" and prefix.parent.name == "native-toolkit" and prefix.parent.parent.name == "out":
        compile_sysroot_base = prefix.parent.parent / "work"
        if compile_sysroot_base.resolve() != compile_sysroot_base:
            raise ToolkitError("private compile sysroot base cannot be a symlink to another directory")
    helper_output = prefix / "bin/distro-seed-install.new"
    scope_flags = (["-DCD_COMPILE_SYSROOT_BASE=" + json.dumps(str(compile_sysroot_base))]
                   if compile_sysroot_base else [])
    _run([environment.get("CC", "cc"), "-O2", str(helper_source), "-o",
          str(helper_output), *scope_flags, *flags,
          "-Wl,-rpath," + str(prefix / "lib")], env=compile_env, log=log)
    helper_output.replace(prefix / "bin/distro-seed-install")
    record = {"schema_version": 1, "development_only": development,
              "source_tree": str(source_dir), "build_options": options,
              "dependencies": _run(["pkg-config", "--modversion", "libarchive", "libcrypto"], env=environment).splitlines(),
              "seed_environment": dict(env or {}), "log": str(log),
              "bootstrap_helper_sha256": hashlib.sha256(helper_source.read_bytes()).hexdigest(),
              "compile_sysroot_base": str(compile_sysroot_base) if compile_sysroot_base else None,
              "tool_hashes": {name: hashlib.sha256((prefix / name).read_bytes()).hexdigest()
                              for name in ("bin/pacman", "bin/distro-seed-install", "lib/libalpm.so.16.0.0")}}
    (prefix / "toolkit.json").write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    return PacmanToolkit(prefix)


class PacmanToolkit:
    def __init__(self, prefix: Path):
        self.prefix = Path(prefix).resolve()
        self.pacman = self.prefix / "bin/pacman"
        if not self.pacman.is_file() or not (self.prefix / "toolkit.json").is_file():
            raise ToolkitError(f"source-built toolkit is not present at {self.prefix}")
        self.record = json.loads((self.prefix / "toolkit.json").read_text())
        for name, expected in self.record.get("tool_hashes", {}).items():
            artifact = self.prefix / name
            if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
                raise ToolkitError(f"native toolkit artifact digest differs: {name}")

    @property
    def development_only(self) -> bool:
        return self.record.get("development_only", True)

    def _env(self) -> dict[str, str]:
        env = {"PATH": str(self.prefix / "bin") + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
               "LC_ALL": "C", "LD_LIBRARY_PATH": str(self.prefix / "lib"),
               "XDG_CACHE_HOME": str(self.prefix / "cache"),
               "XDG_CONFIG_HOME": str(self.prefix / "config"),
               "TMPDIR": str(self.prefix / "tmp")}
        for key in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "TMPDIR"):
            Path(env[key]).mkdir(exist_ok=True)
        return env

    @staticmethod
    def _root(root: Path) -> Path:
        root = Path(root).resolve()
        if root in {Path("/"), Path("/usr"), Path("/usr/local")}:
            raise ToolkitError("refusing to operate on the host root")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def inspect_native(self, package: Path) -> str:
        inspect_package(package)
        return _run([str(self.pacman), "--config", str(self._config()), "-Qip", str(Path(package).resolve())], env=self._env())

    def create_repository(self, directory: Path, packages: Sequence[Path], *,
                          name: str = "distro") -> dict:
        """Produce a real repo-add database for local development testing.

        Signing and production channel publication deliberately require the
        separate release service; this method never changes a running channel.
        """
        if not self.development_only:
            raise ToolkitError("production repositories require the qualified signing/publishing service")
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in name):
            raise ToolkitError("invalid repository name")
        directory = self._root(directory)
        database = directory / f"{name}.db.tar.gz"
        if database.exists():
            raise ToolkitError("repository directory already has a snapshot; use a fresh directory")
        identities, inputs = {}, []
        for package in packages:
            package = Path(package).resolve()
            info = inspect_package(package)
            if info["name"] in identities:
                raise ToolkitError(f"snapshot has duplicate package name: {info['name']}")
            identities[info["name"]] = {"version": info["version"], "sha256": info["sha256"],
                                       "filename": package.name}
            destination = directory / package.name
            if destination.exists() and destination.read_bytes() != package.read_bytes():
                raise ToolkitError(f"immutable repository artifact collision: {package.name}")
            if destination != package and not destination.exists():
                shutil.copyfile(package, destination)
            inputs.append(str(destination))
        if not inputs:
            raise ToolkitError("cannot publish an empty development snapshot")
        _run([str(self.prefix / "bin/repo-add"), str(database), *inputs], env=self._env())
        record = {"schema_version": 1, "development_only": True, "repository": name,
                  "database": str(database), "packages": identities}
        (directory / "snapshot.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        return record

    def _config(self) -> Path:
        config = self.prefix / "etc/custom-distro-local.conf"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("[options]\nArchitecture = x86_64\nSigLevel = " +
                          ("Never" if self.development_only else "Required TrustedOnly") + "\n")
        return config

    def install(self, root: Path, packages: Sequence[Path], *, bootstrap: bool = False,
                config: Path | None = None, expected_hashes: Mapping[str, str] | None = None,
                architecture: str = "x86_64", compile_sysroot: bool = False) -> str:
        """Install through libalpm, retaining its real installed-file database.

        Development-only empty-root installs use the seed helper, which disables
        BOTH scriptlets and hooks via ALPM flags. fakeroot supports unprivileged
        local image assembly; it cannot execute target-root scriptlets/chroot.
        Explicit private compile sysroots omit runtime dependency checks, while
        preserving package conflicts, file ownership and installed metadata.
        """
        if compile_sysroot:
            expected_base = self.prefix.parent.parent / "work"
            scoped_root = Path(root).resolve()
            private_toolkit = (self.prefix.name == "prefix" and self.prefix.parent.name == "native-toolkit"
                               and self.prefix.parent.parent.name == "out")
            if (not bootstrap or not self.development_only or config is not None or not private_toolkit
                    or self.record.get("compile_sysroot_base") != str(expected_base)
                    or expected_base.resolve() != expected_base
                    or not scoped_root.is_relative_to(expected_base)
                    or len(scoped_root.relative_to(expected_base).parts) < 2
                    or scoped_root.name != "sysroot"):
                raise ToolkitError("compile sysroot mode requires this project's generated out/work/.../sysroot")
        root = self._root(root)
        database = root / "var/lib/pacman"
        database.mkdir(parents=True, exist_ok=True)
        archives = []
        for path in packages:
            path = Path(path).resolve()
            info = inspect_package(path)
            if info["architecture"] not in {architecture, "any"}:
                raise ToolkitError(f"wrong package architecture: {path.name}")
            if expected_hashes is not None and expected_hashes.get(path.name) != info["sha256"]:
                raise ToolkitError(f"package digest differs from snapshot: {path.name}")
            archives.append(str(path))
        if not archives:
            raise ToolkitError("no package archives supplied")
        environment = self._env()
        if bootstrap:
            if not self.development_only:
                raise ToolkitError("unsigned seed helper is only allowed for development toolkit")
            command = [str(self.prefix / "bin/distro-seed-install"),
                       *(["--compile-sysroot"] if compile_sysroot else []), str(root), str(database),
                       architecture, *archives]
        else:
            if self.development_only:
                raise ToolkitError("development toolkit only permits explicit local bootstrap installs")
            if config is None:
                raise ToolkitError("signed ordinary installs require a distro trust/repository config")
            command = [str(self.pacman), "--config", str(Path(config).resolve()),
                       "--root", str(root), "--dbpath", str(database), "--logfile",
                       str(root / "var/log/pacman.log"), "--cachedir", str(root / "var/cache/pacman/pkg"),
                       "--gpgdir", str(root / "etc/pacman.d/gnupg"), "--arch", architecture,
                       "--noconfirm", "-U", *archives]
        if os.geteuid() != 0:
            if not bootstrap or shutil.which("fakeroot") is None:
                raise ToolkitError("root privileges required; local bootstrap supports fakeroot")
            command = [shutil.which("fakeroot"), *command]
        return _run(command, env=environment)

    def query(self, root: Path, *arguments: str) -> str:
        root = self._root(root)
        return _run([str(self.pacman), "--config", str(self._config()), "--root", str(root),
                     "--dbpath", str(root / "var/lib/pacman"), "-Q", *arguments], env=self._env())

    def remove(self, root: Path, names: Sequence[str]) -> str:
        """Development removal uses the same hook/scriptless libalpm transaction."""
        if not self.development_only:
            raise ToolkitError("production removal requires the installed system's trusted pacman config")
        root = self._root(root)
        if not names or any(not name or name.startswith("-") or "/" in name for name in names):
            raise ToolkitError("invalid package names for removal")
        command = [str(self.prefix / "bin/distro-seed-install"), "--remove", str(root),
                   str(root / "var/lib/pacman"), "x86_64", *names]
        if os.geteuid() != 0:
            if not shutil.which("fakeroot"):
                raise ToolkitError("fakeroot is needed for development removal")
            command = [shutil.which("fakeroot"), *command]
        return _run(command, env=self._env())
