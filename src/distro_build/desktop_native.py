"""Source-built native desktop libraries for the independent target sysroot.

The host scanner/template generators are built in private work directories and
are kept out of target packages. Every target ELF is audited against the package
dependency sysroot before export. No installed host graphics libraries are used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .packaging.target_build import TargetBuild


def _link_wrapper(path: Path, compiler: Path, sysroot: Path, allowed: list[Path]) -> None:
    script = ("#!/usr/bin/python3\nimport os,pathlib,re,sys\n"
              f"compiler={str(compiler)!r}\nsysroot={str(sysroot)!r}\n"
              f"allowed={[str(p.resolve()) for p in allowed]!r}\n"
              "def check(value):\n"
              "    if value.startswith('/') and not any(pathlib.Path(value).resolve().is_relative_to(root) for root in allowed):\n"
              "        sys.exit('Target compiler refused a host include/library path: '+value)\n"
              "arguments=sys.argv[1:]\n"
              "for index,argument in enumerate(arguments):\n"
              "    if argument in ('-L','-I','-isystem','-iquote') and index+1<len(arguments):\n"
              "        check(arguments[index+1])\n"
              "    elif argument.startswith(('-L','-I')) and len(argument)>2:\n"
              "        check(argument[2:])\n"
              "    elif re.search(r'\\.(?:so(?:\\.\\d+)*|a|o|rlib)$',argument):\n"
              "        check(argument)\n"
              "    elif argument.startswith('-Wl,'):\n"
              "        tokens=argument[4:].split(',')\n"
              "        for token in tokens:\n"
              "            if token.startswith('-L'):\n"
              "                check(token[2:])\n"
              "            elif token.startswith('/'):\n"
              "                check(token)\n"
              "options=['--sysroot='+sysroot,'-march=x86-64','-mtune=generic']\n"
              "if not any(argument in arguments for argument in ('-c','-E','-S','-M','-MM')):\n"
              "    options+=['-Wl,--dynamic-linker=/lib64/ld-linux-x86-64.so.2']\n"
              "os.execv(compiler,[compiler,*options,*arguments])\n")
    path.write_text(script)
    path.chmod(0o755)


class DesktopBuild(TargetBuild):
    def __init__(self) -> None:
        super().__init__()
        pass2 = self.tools / "pass2/bin" / f"{self.target}-gcc"
        if pass2.is_file() and (self.tools / "pass2/.runtime-validated.json").is_file():
            self.cc = pass2
        allowed = [self.sysroot, self.tools, self.work]
        self.wrapper = self.work / "target-cc"
        _link_wrapper(self.wrapper, self.cc, self.sysroot, allowed)
        self.env["CC"] = str(self.wrapper)
        self.env["CFLAGS"] = "-O2 -g0 -fPIC -march=x86-64 -mtune=generic"
        self.env["CPPFLAGS"] = ""
        self.native = self.work / "native-tools"
        self.native.mkdir()
        self.native_pc = self.work / "native-pkg-config"
        self.native_pc.write_text(
            "#!/usr/bin/python3\nimport os\n"
            "env=dict(os.environ)\n"
            "env.pop('PKG_CONFIG_SYSROOT_DIR',None)\n"
            f"env['PKG_CONFIG_LIBDIR']={str(self.native / 'lib/pkgconfig')!r}\n"
            "env['PKG_CONFIG_PATH']=''\n"
            "os.execve('/usr/bin/pkg-config',['pkg-config',*__import__('sys').argv[1:]],env)\n")
        self.native_pc.chmod(0o755)
        self.native_file = self.work / "native.ini"
        self.native_file.write_text(
            "[binaries]\nc = '/usr/bin/gcc'\ncpp = '/usr/bin/g++'\n"
            f"pkg-config = {str(self.native_pc)!r}\n")

    def meson(self, options: list[str], *, cpp: bool = False, data_only: bool = False,
              installed_only: bool = False) -> None:
        cross = self.work / "cross.ini"
        lines = ["[binaries]", f"c = {str(self.wrapper)!r}",
                 f"ar = {self.env['AR']!r}", f"strip = {self.env['STRIP']!r}",
                 "pkg-config = '/usr/bin/pkg-config'"]
        if cpp:
            compiler = self.tools / "pass2/bin" / f"{self.target}-g++"
            if not compiler.is_file() or not (self.tools / "pass2/.runtime-validated.json").is_file():
                raise RuntimeError("Mesa requires the source-built pass2 C++ compiler")
            wrapper = self.work / "target-cxx"
            _link_wrapper(wrapper, compiler, self.sysroot, [self.sysroot, self.tools, self.work])
            lines.append(f"cpp = {str(wrapper)!r}")
        lines += ["[host_machine]", "system = 'linux'", "cpu_family = 'x86_64'",
                  "cpu = 'x86_64'", "endian = 'little'", "[properties]",
                  "needs_exe_wrapper = true", f"sys_root = {str(self.sysroot)!r}",
                  "pkg_config_libdir = [" + ", ".join(repr(str(self.sysroot / item)) for item in
                      ("usr/lib/pkgconfig", "usr/share/pkgconfig")) + "]",
                  "[built-in options]", "c_args = ['-O2', '-g0', '-fPIC']",
                  "c_link_args = ['-static-libgcc']", "cpp_args = ['-O2', '-g0', '-fPIC']",
                  "cpp_link_args = ['-static-libgcc']"]
        cross.write_text("\n".join(lines) + "\n")
        directory = self.work / "target-build"
        self.run(["meson", "setup", str(directory), str(self.source), "--cross-file", str(cross),
                  "--native-file", str(self.native_file), "--prefix=/usr", "--libdir=lib",
                  "--sysconfdir=/etc", "--localstatedir=/var", "--buildtype=release",
                  "--wrap-mode=nofallback", "-Ddefault_library=shared", *options])
        outputs = []
        if installed_only:
            targets = json.loads(subprocess.check_output(
                ["meson", "introspect", "--targets", str(directory)], env=self.env, text=True))
            outputs = list(dict.fromkeys(
                str(Path(filename).relative_to(directory))
                for target in targets if target.get("installed")
                for filename in target["filename"]))
            if not outputs:
                raise RuntimeError("Meson did not declare any installed build targets")
        self.run(["ninja", "-C", str(directory), f"-j{self.jobs}", *outputs])
        self.env["DESTDIR"] = str(self.stage)
        self.run(["meson", "install", "-C", str(directory), "--no-rebuild"])

    def secondary(self, name: str) -> Path:
        directory = Path(os.environ["CD_SOURCES_DIR"]) / name
        entries = list(directory.iterdir())
        return entries[0] if len(entries) == 1 and entries[0].is_dir() else directory

    def host_env(self) -> dict:
        env = dict(self.env)
        for variable in ("CC", "CXX", "AR", "RANLIB", "NM", "STRIP", "CFLAGS", "CXXFLAGS", "CPPFLAGS",
                         "LDFLAGS", "PKG_CONFIG_SYSROOT_DIR", "DESTDIR"):
            env.pop(variable, None)
        env.update({"CC": "/usr/bin/gcc", "CXX": "/usr/bin/g++", "AR": "/usr/bin/ar",
                    "CFLAGS": "-O2 -g0", "CXXFLAGS": "-O2 -g0", "PKG_CONFIG_PATH": "",
                    "PKG_CONFIG_LIBDIR": str(self.native / "lib/pkgconfig")})
        return env

    def host_run(self, arguments: list[str], cwd: Path, env: dict | None = None) -> None:
        print(json.dumps({"native_generator_command": arguments, "cwd": str(cwd)}), flush=True)
        subprocess.run(arguments, cwd=cwd, env=env or self.host_env(), check=True)

    def gperf(self) -> None:
        source = self.secondary("gperf")
        directory = self.work / "native-gperf"
        directory.mkdir()
        self.host_run([str(source / "configure"), f"--prefix={self.native}"], directory)
        self.host_run(["make", f"-j{self.jobs}"], directory)
        self.host_run(["make", "install"], directory)
        self.env["GPERF"] = str(self.native / "bin/gperf")

    def scanner(self) -> None:
        """Build a host scanner from the same Wayland release and private expat."""
        source = self.secondary("expat")
        directory = self.work / "native-expat"
        directory.mkdir()
        self.host_run([str(source / "configure"), f"--prefix={self.native}", "--disable-shared",
                       "--enable-static", "--without-docbook", "--without-examples", "--without-tests"], directory)
        self.host_run(["make", f"-j{self.jobs}"], directory)
        self.host_run(["make", "install"], directory)
        directory = self.work / "native-wayland"
        self.host_run(["meson", "setup", str(directory), str(self.source if self.source.name.startswith("wayland-") and not self.source.name.startswith("wayland-protocols-") else self.secondary("wayland")),
                       "--native-file", str(self.native_file), f"--prefix={self.native}", "--libdir=lib",
                       "--buildtype=release", "--wrap-mode=nofallback", "-Dlibraries=false",
                       "-Dscanner=true", "-Ddtd_validation=false", "-Ddocumentation=false", "-Dtests=false"], self.work)
        self.host_run(["ninja", "-C", str(directory), f"-j{self.jobs}"], self.work)
        self.host_run(["meson", "install", "-C", str(directory), "--no-rebuild"], self.work)
        scanner = self.native / "bin/wayland-scanner"
        self.env["PATH"] = str(self.native / "bin") + os.pathsep + self.env["PATH"]
        (self.work / "native-generator.json").write_text(json.dumps(
            {"kind": "source-built-private-host-tool", "path": str(scanner),
             "sha256": hashlib.sha256(scanner.read_bytes()).hexdigest(),
             "source": "same pinned Wayland release with pinned private static expat"}, indent=2) + "\n")

    def templates(self) -> None:
        # Mako and MarkupSafe both have pure-Python implementations. Keep these
        # private source inputs out of the target OS and global Python installs.
        paths = [self.secondary("mako"), self.secondary("markupsafe") / "src"]
        self.env["PYTHONPATH"] = os.pathsep.join(str(path) for path in paths)
        self.run(["python3", "-c", "import mako,markupsafe; print(mako.__version__)"])


def build_desktop_native(name: str) -> None:
    build = DesktopBuild()
    data_only = False
    if name == "libffi":
        build.autotools(["--disable-static", "--disable-docs"])
        build.license(name, ["LICENSE"])
    elif name == "expat":
        build.autotools(["--disable-static", "--without-docbook", "--without-examples", "--without-tests",
                         "--without-xmlwf"])
        build.license(name, ["COPYING"])
    elif name == "wayland":
        build.scanner()
        build.meson(["-Ddocumentation=false", "-Dtests=false", "-Ddtd_validation=false"])
        build.license(name, ["COPYING"])
    elif name == "wayland-protocols":
        build.scanner()
        build.meson(["-Dtests=false"], data_only=True)
        build.license(name, ["COPYING"])
        data_only = True
    elif name == "libdrm":
        build.meson(["-Dtests=false", "-Dman-pages=disabled", "-Dvalgrind=disabled", "-Dcairo-tests=disabled",
                     *[f"-D{driver}=disabled" for driver in
                       ("intel", "radeon", "amdgpu", "nouveau", "vmwgfx", "omap", "exynos", "freedreno", "tegra", "vc4", "etnaviv")]])
        build.license(name, ["xf86drm.h", "xf86drm.c"])
    elif name == "libxkbcommon":
        build.meson(["-Denable-x11=false", "-Denable-wayland=false", "-Denable-docs=false",
                     "-Denable-tools=false", "-Denable-xkbregistry=false", "-Denable-bash-completion=false",
                     "-Dxkb-config-root=/usr/share/X11/xkb"])
        build.license(name, ["LICENSE"])
    elif name == "xkeyboard-config":
        build.meson(["-Dnls=false"], data_only=True)
        build.license(name, ["COPYING"])
        data_only = True
    elif name == "libevdev":
        build.meson(["-Dtests=disabled", "-Dtools=disabled", "-Ddocumentation=disabled"])
        build.license(name, ["COPYING"])
    elif name == "mtdev":
        build.autotools(["--disable-static"])
        build.license(name, ["COPYING"])
    elif name == "libudev":
        build.gperf()
        build.autotools(["--disable-static", "--disable-blkid", "--disable-kmod", "--disable-selinux",
                         "--disable-manpages", "--disable-hwdb", "--disable-rule-generator", "--disable-mtd_probe",
                         "--sysconfdir=/etc", "--localstatedir=/var", "--with-rootprefix=/usr", "--with-rootlibdir=/usr/lib", "--with-rootlibexecdir=/usr/lib/udev"])
        build.license(name, ["COPYING"])
    elif name == "libseat":
        build.meson(["-Dlibseat-logind=disabled", "-Dlibseat-seatd=enabled", "-Dlibseat-builtin=disabled",
                     "-Dserver=enabled", "-Dexamples=disabled", "-Dman-pages=disabled"])
        build.license(name, ["LICENSE"])
    elif name == "libinput":
        build.meson(["-Dlibwacom=false", "-Dmtdev=true", "-Ddebug-gui=false", "-Dtests=false",
                     "-Ddocumentation=false", "-Dlua-plugins=disabled", "-Dzshcompletiondir=no"])
        build.license(name, ["COPYING"])
    elif name == "mesa-gbm":
        build.templates()
        build.meson(["-Dgbm=enabled", "-Dgallium-drivers=[]", "-Dvulkan-drivers=[]", "-Dplatforms=[]",
                     "-Dglx=disabled", "-Degl=disabled", "-Dopengl=false", "-Dgles1=disabled", "-Dgles2=disabled",
                     "-Dllvm=disabled", "-Dzstd=disabled", "-Dzlib=disabled", "-Dshader-cache=disabled", "-Dxmlconfig=disabled",
                     "-Dglvnd=disabled", "-Dvalgrind=disabled", "-Dlibunwind=disabled", "-Dbuild-tests=false"], cpp=True)
        build.license(name, ["docs/license.rst"])
        description = build.stage / "usr/share/doc/mesa-gbm/qualification.txt"
        description.parent.mkdir(parents=True)
        description.write_text("GBM interface for Telorgon's CPU KMS renderer. No OpenGL, EGL, Vulkan or Gallium drivers.\n")
    else:
        raise RuntimeError(f"Unknown desktop target library: {name}")
    for path in build.stage.rglob("*.la"):
        path.unlink()
    if data_only:
        if not any(path.is_file() for path in build.stage.rglob("*")):
            raise RuntimeError("Empty target protocol/keymap data package")
    else:
        build.audit()
