"""Source-built target libraries and the experimental local pacman client.

Host Python, make, Perl, Meson and pkg-config coordinate cross compilation; their
libraries are never searched as target dependencies. Installation always uses a
private DESTDIR. This module is deliberately separate from the native toolkit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


class TargetBuild:
    def __init__(self) -> None:
        self.source = Path(os.environ["CD_SOURCE_DIR"])
        self.work = Path(os.environ["CD_WORK_DIR"])
        self.stage = Path(os.environ["CD_STAGE_DIR"])
        self.sysroot = Path(os.environ["CD_SYSROOT"])
        self.tools = Path(os.environ["CD_TOOLS"])
        self.target = os.environ["CD_TARGET"]
        self.jobs = os.environ["CD_JOBS"]
        if os.environ.get("CD_BUILD_MODE") != "target":
            raise RuntimeError("package-manager stack requires the source-built target bootstrap")
        if not (self.sysroot / ".bootstrap-validated.json").is_file():
            raise RuntimeError("target dependency sysroot lacks the validated bootstrap marker")
        self.cc = Path(os.environ.get("CD_CC", str(self.tools / "bin" / f"{self.target}-gcc")))
        self.readelf = self.tools / "bin" / f"{self.target}-readelf"
        self.env = dict(os.environ)
        self.env.update({
            "CC": f"{self.cc} --sysroot={self.sysroot}",
            "AR": str(self.tools / "bin" / f"{self.target}-ar"),
            "RANLIB": str(self.tools / "bin" / f"{self.target}-ranlib"),
            "NM": str(self.tools / "bin" / f"{self.target}-nm"),
            "STRIP": str(self.tools / "bin" / f"{self.target}-strip"),
            "CFLAGS": "-O2 -g0 -fPIC",
            "LDFLAGS": "-static-libgcc",
            "PKG_CONFIG_SYSROOT_DIR": str(self.sysroot),
            "PKG_CONFIG_LIBDIR": f"{self.sysroot}/usr/lib/pkgconfig:{self.sysroot}/usr/share/pkgconfig",
            "PKG_CONFIG_PATH": "",
            "XDG_CACHE_HOME": str(self.work / "cache"),
            "XDG_CONFIG_HOME": str(self.work / "config"),
            "TMPDIR": str(self.work / "tmp"),
        })
        for variable in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "TMPDIR"):
            Path(self.env[variable]).mkdir(parents=True, exist_ok=True)
        for variable in ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH", "LD_LIBRARY_PATH"):
            self.env.pop(variable, None)

    def run(self, arguments: list[str], cwd: Path | None = None) -> None:
        print(json.dumps({"target_command": arguments, "cwd": str(cwd or self.source)}), flush=True)
        subprocess.run(arguments, cwd=cwd or self.source, env=self.env, check=True)

    def license(self, name: str, filenames: list[str]) -> None:
        destination = self.stage / "usr/share/licenses" / name
        destination.mkdir(parents=True, exist_ok=True)
        for filename in filenames:
            shutil.copy2(self.source / filename, destination / Path(filename).name)

    def autotools(self, options: list[str], required_macros: tuple[str, ...] = ()) -> None:
        build = self.work / "target-build"
        build.mkdir()
        guess = next(self.source.glob("**/config.guess"))
        triplet = subprocess.check_output(["/bin/sh", str(guess)], text=True).strip()
        self.run([str(self.source / "configure"), "--prefix=/usr", "--libdir=/usr/lib",
                  f"--host={self.target}", f"--build={triplet}", *options], build)
        if required_macros:
            configuration = (build / "config.h").read_text()
            missing = [name for name in required_macros
                       if not re.search(rf"^#define {re.escape(name)} 1$", configuration, re.M)]
            if missing:
                raise RuntimeError(f"required source-built target archive features were not detected: {missing}")
        self.run(["make", f"-j{self.jobs}"], build)
        self.run(["make", f"DESTDIR={self.stage}", "install"], build)

    def audit(self) -> None:
        """Reject host run paths and unresolved shared-library requirements."""
        provided = set()
        for root in (self.stage, self.sysroot):
            for directory in ("usr/lib", "lib", "lib64"):
                path = root / directory
                if path.exists():
                    provided.update(child.name for child in path.iterdir())
        audited = []
        for path in sorted(self.stage.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
            dynamic = subprocess.check_output([str(self.readelf), "-d", str(path)], text=True)
            program = subprocess.check_output([str(self.readelf), "-l", str(path)], text=True)
            if "(RPATH)" in dynamic or "(RUNPATH)" in dynamic:
                raise RuntimeError(f"target ELF contains an embedded library search path: {path}")
            needed = re.findall(r"\(NEEDED\).*\[([^]]+)\]", dynamic)
            absent = set(needed) - provided
            if absent:
                raise RuntimeError(f"target ELF dependencies not supplied by the dependency sysroot: {path}: {sorted(absent)}")
            interpreters = re.findall(r"Requesting program interpreter: ([^]]+)", program)
            if any(item != "/lib64/ld-linux-x86-64.so.2" for item in interpreters):
                raise RuntimeError(f"unexpected target interpreter: {path}: {interpreters}")
            audited.append({"file": str(path.relative_to(self.stage)), "needed": needed, "interpreter": interpreters})
        if not audited:
            raise RuntimeError("target package contains no compiled ELF payload")
        (self.work / "target-elf-audit.json").write_text(json.dumps(audited, indent=2) + "\n")

    def probe(self, binary: Path, *arguments: str) -> str:
        loader = self.sysroot / "usr/lib/ld-linux-x86-64.so.2"
        result = subprocess.run([str(loader), "--library-path",
                                 f"{self.stage}/usr/lib:{self.sysroot}/usr/lib", str(binary), *arguments],
                                env=self.env, text=True, capture_output=True, check=True)
        print(result.stdout, end="", flush=True)
        return result.stdout


def build_target(name: str) -> None:
    build = TargetBuild()
    if name == "xz":
        build.autotools(["--disable-nls", "--disable-doc"])
        build.license(name, ["COPYING", "COPYING.GPLv2", "COPYING.LGPLv2.1"])
        build.probe(build.stage / "usr/bin/xz", "--version")
    elif name == "zlib":
        build.run(["./configure", "--prefix=/usr", "--libdir=/usr/lib"])
        build.run(["make", f"-j{build.jobs}"])
        build.run(["make", f"DESTDIR={build.stage}", "install"])
        build.license(name, ["LICENSE"])
    elif name == "openssl":
        build.run(["perl", "Configure", "linux-x86_64", "--prefix=/usr", "--libdir=lib",
                   "--openssldir=/etc/ssl", "shared", "no-tests"])
        build.run(["make", f"-j{build.jobs}"])
        build.run(["make", f"DESTDIR={build.stage}", "install_sw"])
        # OpenSSL's built-in rehash command avoids the optional Perl wrapper.
        (build.stage / "usr/bin/c_rehash").unlink(missing_ok=True)
        build.license(name, ["LICENSE.txt"])
        build.probe(build.stage / "usr/bin/openssl", "version")
    elif name == "libarchive":
        build.autotools(["--without-zstd", "--without-bz2lib", "--without-lz4", "--without-xml2",
                         "--without-expat", "--without-iconv", "--without-libb2", "--disable-acl",
                         "--disable-xattr", "--disable-rpath", f"--with-sysroot={build.sysroot}",
                         "--with-zlib", "--with-lzma", "--with-openssl"],
                        ("HAVE_LIBZ", "HAVE_LIBLZMA", "HAVE_OPENSSL_EVP_H"))
        build.license(name, ["COPYING"])
        build.probe(build.stage / "usr/bin/bsdtar", "--version")
        from .sdk_metadata import normalize_sdk_metadata
        metadata = normalize_sdk_metadata(build.stage, build.sysroot, name)
        (build.work / "sdk-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    elif name == "pacman":
        _pacman(build)
    else:
        raise RuntimeError(f"unsupported target package: {name}")
    # Shared-library consumers use pkg-config; libtool archives can retain
    # temporary dependency-sysroot paths and must not enter installed packages.
    for path in build.stage.rglob("*.la"):
        path.unlink()
    build.audit()


def _pacman(build: TargetBuild) -> None:
    profile = os.environ.get("CD_PACMAN_PROFILE", "secure")
    if profile not in {"secure", "local-development"}:
        raise RuntimeError(f"Unknown pacman trust profile: {profile}")
    cross = build.work / "meson-cross.ini"
    quote = lambda value: repr(str(value))
    cross.write_text("\n".join([
        "[binaries]", f"c = [{quote(build.cc)}, {quote('--sysroot=' + str(build.sysroot))}]",
        f"ar = {quote(build.env['AR'])}", f"strip = {quote(build.env['STRIP'])}", "pkg-config = '/usr/bin/pkg-config'",
        "[host_machine]", "system = 'linux'", "cpu_family = 'x86_64'", "cpu = 'x86_64'", "endian = 'little'",
        "[properties]", "needs_exe_wrapper = true", f"sys_root = {quote(build.sysroot)}",
        "pkg_config_libdir = [" + ", ".join(quote(build.sysroot / item) for item in ("usr/lib/pkgconfig", "usr/share/pkgconfig")) + "]",
        "[built-in options]", "c_args = ['-O2', '-g0']", "c_link_args = ['-static-libgcc']", "",
    ]))
    directory = build.work / "target-build"
    build.run(["meson", "setup", str(directory), str(build.source), "--cross-file", str(cross),
               "--prefix=/usr", "--libdir=lib", "--sysconfdir=/etc", "--localstatedir=/var",
               "--buildtype=release", "-Ddoc=disabled", "-Di18n=false", "-Dcurl=enabled",
               "-Dgpgme=enabled", "-Dcrypto=openssl", "-Dpkg-ext=.pkg.tar.xz"])
    build.run(["ninja", "-C", str(directory), f"-j{build.jobs}"])
    build.env["DESTDIR"] = str(build.stage)
    build.run(["meson", "install", "-C", str(directory), "--no-rebuild"])
    # makepkg/repo-add require target Bash and additional programs. Assembly uses
    # the separately source-built native toolkit until that stack is packaged.
    for filename in ("makepkg", "makepkg-template", "pacman-key", "pacman-db-upgrade",
                     "repo-add", "repo-remove", "repo-elephant"):
        (build.stage / "usr/bin" / filename).unlink(missing_ok=True)
    for directory_name in ("usr/share/makepkg", "usr/share/makepkg-template", "usr/share/bash-completion"):
        shutil.rmtree(build.stage / directory_name, ignore_errors=True)
    (build.stage / "etc/makepkg.conf").unlink(missing_ok=True)
    (build.stage / "usr/share/pkgconfig/libmakepkg.pc").unlink(missing_ok=True)
    shutil.rmtree(build.stage / "etc/makepkg.conf.d", ignore_errors=True)
    helper = build.stage / "usr/libexec/custom-distro/local-fetch"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("""#!/bin/sh
# Experimental file-only transport for standard libalpm transactions.
set -eu
[ "$#" -eq 2 ] || exit 2
case "$1" in file:///*) source=${1#file://} ;; *) echo 'Only file:// repositories are enabled in this experimental build' >&2; exit 1 ;; esac
[ -f "$source" ] || exit 1
cp "$source" "$2"
""")
    helper.chmod(0o755)
    if profile == "local-development":
        configuration = """# Explicit local development policy. Never use for published updates.
[options]
Architecture = x86_64
SigLevel = Never
LocalFileSigLevel = Never
XferCommand = /usr/libexec/custom-distro/local-fetch %u %o

[distro]
Server = file:///var/lib/custom-distro/repository
"""
    else:
        configuration = """# Require a distro-managed public keyring before installing updates.
[options]
Architecture = x86_64
SigLevel = Required DatabaseRequired
LocalFileSigLevel = Required
GPGDir = /etc/pacman.d/gnupg

[distro]
Server = file:///var/lib/custom-distro/repository
"""
    (build.stage / "etc/pacman.conf").write_text(configuration)
    (build.work / "target-pacman-policy.json").write_text(json.dumps(
        {"profile": profile, "gpgme": True, "curl": True,
         "signatures_required": profile == "secure", "trust_keyring_bundled": False}, indent=2) + "\n")
    build.license("pacman", ["COPYING"])
    version = build.probe(build.stage / "usr/bin/pacman", "--version")
    if "without GPGME" in version:
        raise RuntimeError("Source-built pacman lacks required GPGME signature verification")
