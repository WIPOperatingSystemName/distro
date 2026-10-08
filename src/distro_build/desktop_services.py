"""Source-built desktop services and private SDK generators.

Target libraries are obtained only from the declared package dependency sysroot.
Native Python generators use the corresponding packaged GLib source modules;
compiled target generators run through the same sysroot's explicit loader.
"""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
import shutil
import subprocess
import textwrap
import xml.etree.ElementTree as ET

from .desktop_native import DesktopBuild
from .runtime_native import RuntimeBuild


class ServiceBuild(DesktopBuild):
    closed_loader = RuntimeBuild.closed_loader
    closed_probe = RuntimeBuild.closed_probe

    def __init__(self) -> None:
        super().__init__()
        # pkgconf otherwise rewrites target installation-directory variables
        # with the build sysroot, even though their value becomes guest paths.
        # FDO rules retain sysroot handling for compiler include/library flags.
        self.env["PKG_CONFIG_FDO_SYSROOT_RULES"] = "1"

    def audit_payload_paths(self) -> None:
        findings = []
        rejected = []
        prefix = re.compile(rb"/[^\x00\n\r\t ]*/out/[^\x00\n\r\t ]+")
        for path in sorted(self.stage.rglob("*")):
            relative = path.relative_to(self.stage).as_posix()
            if relative.startswith(("home/", "tmp/")):
                rejected.append({"path": relative, "reason": "build directory in package installation path"})
            if path.is_symlink():
                target = path.readlink().as_posix()
                if "/out/" in target or target.startswith("/home/"):
                    rejected.append({"path": relative, "reason": "build directory in symlink target", "target": target})
                continue
            if not path.is_file():
                continue
            content = path.read_bytes()
            if content.startswith(b"\x7fELF"):
                # Allocated sections include runtime constants; debug-only
                # glibc/GCC source filenames are not guest directory lookups.
                sections = subprocess.check_output(["readelf", "-SW", str(path)], text=True)
                chunks = []
                for match in re.finditer(r"\[\s*\d+\]\s+(\S+)\s+(\S+)\s+[0-9a-f]+\s+([0-9a-f]+)\s+([0-9a-f]+)\s+[0-9a-f]+\s+([A-Z]*)", sections):
                    if "A" in match[5] and match[2] != "NOBITS":
                        offset, size = int(match[3], 16), int(match[4], 16)
                        chunks.append(content[offset:offset + size])
                content = b"\0".join(chunks)
            for match in prefix.finditer(content):
                value = match[0].decode("utf-8", "replace")
                record = {"path": relative, "value": value}
                if value.endswith((".h", ".c", ".cpp", ".rs")):
                    findings.append({**record, "kind": "diagnostic source filename"})
                else:
                    rejected.append({**record, "reason": "build directory in runtime payload"})
        report = {"passed": not rejected, "diagnostic_source_names": findings, "rejected": rejected,
                  "scope": "installation paths, symlink targets, text/data and allocated ELF constants; debug source directories excluded"}
        (self.work / "target-payload-path-audit.json").write_text(json.dumps(report, indent=2) + "\n")
        if rejected:
            raise RuntimeError("Target payload contains build installation/runtime paths: " + json.dumps(rejected))

    def gettext_generators(self) -> None:
        from .gettext_native import prepare_gettext_tools
        binary = prepare_gettext_tools(self.sysroot, self.work)
        self.env["PATH"] = str(binary) + ":" + self.env["PATH"]

    def glib_generators(self) -> None:
        binaries = self.native / "bin"
        binaries.mkdir(parents=True, exist_ok=True)
        modules = self.sysroot / "usr/share/glib-2.0"
        for name in ("gdbus-codegen", "glib-mkenums", "glib-genmarshal"):
            program = modules / "sdk-tools" / name
            if not program.is_file():
                raise RuntimeError(f"Source-built GLib SDK generator missing: {program}")
            wrapper = binaries / name
            wrapper.write_text("#!/usr/bin/python3\nimport os,runpy,sys\n"
                f"sys.path.insert(0,{str(modules)!r})\n"
                f"sys.argv[0]={str(program)!r}\nrunpy.run_path({str(program)!r},run_name='__main__')\n")
            wrapper.chmod(0o755)
        for name in ("glib-compile-resources", "glib-compile-schemas"):
            program = self.sysroot / "usr/bin" / name
            self.closed_loader(program)
            wrapper = binaries / name
            wrapper.write_text("#!/usr/bin/python3\nimport os,sys\n"
                f"loader={str(self.sysroot / 'usr/lib/ld-linux-x86-64.so.2')!r}\n"
                f"os.execv(loader,[loader,'--library-path',{str(self.sysroot / 'usr/lib')!r},{str(program)!r},*sys.argv[1:]])\n")
            wrapper.chmod(0o755)
        self.env["PATH"] = str(binaries) + ":" + self.env["PATH"]
        self.env.update({"GIO_MODULE_DIR": str(self.sysroot / "usr/lib/gio/modules"),
                         "GIO_USE_VFS": "local", "GSETTINGS_BACKEND": "memory",
                         "PYTHONDONTWRITEBYTECODE": "1"})
        # Meson's GNOME module reads generator variables from the target .pc
        # files. Redirect only the private sysroot's development variables.
        for pc in (self.sysroot / "usr/lib/pkgconfig").glob("*.pc"):
            content = pc.read_text()
            for name in ("gdbus-codegen", "glib-mkenums", "glib-genmarshal", "glib-compile-resources", "glib-compile-schemas"):
                content = content.replace("${bindir}/" + name, str(binaries / name))
            pc.write_text(content)
        entries = "\n".join(f"{name} = {str(binaries / name)!r}" for name in
            ("gdbus-codegen", "glib-mkenums", "glib-genmarshal", "glib-compile-resources", "glib-compile-schemas"))
        with self.native_file.open("a") as stream:
            stream.write(entries + "\n")


def build_desktop_service(name: str) -> None:
    b = ServiceBuild()
    if name == "pcre2":
        # Upstream's CMake install avoids libtool relinking against /usr/lib.
        cmake = Path(shutil.which("cmake"))
        (b.work / "cmake-seed.json").write_text(json.dumps({"kind": "declared-host-build-generator",
            "path": str(cmake), "sha256": hashlib.sha256(cmake.read_bytes()).hexdigest()}) + "\n")
        directory = b.work / "target-build"
        b.run([str(cmake), "-S", str(b.source), "-B", str(directory), "-G", "Ninja",
            "-DCMAKE_SYSTEM_NAME=Linux", "-DCMAKE_SYSTEM_PROCESSOR=x86_64",
            f"-DCMAKE_C_COMPILER={b.wrapper}", f"-DCMAKE_SYSROOT={b.sysroot}",
            f"-DCMAKE_FIND_ROOT_PATH={b.sysroot}", "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY",
            "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY", "-DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY",
            "-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_INSTALL_PREFIX=/usr", "-DCMAKE_INSTALL_LIBDIR=lib", "-DCMAKE_SKIP_RPATH=ON",
            "-DBUILD_SHARED_LIBS=ON", "-DBUILD_STATIC_LIBS=OFF", "-DPCRE2_BUILD_PCRE2_16=ON",
            "-DPCRE2_BUILD_PCRE2_32=ON", "-DPCRE2_SUPPORT_UNICODE=ON", "-DPCRE2_SUPPORT_JIT=ON",
            "-DPCRE2_SUPPORT_LIBZ=OFF", "-DPCRE2_SUPPORT_LIBBZ2=OFF", "-DPCRE2_SUPPORT_LIBREADLINE=OFF",
            "-DPCRE2_BUILD_TESTS=OFF"])
        b.run(["ninja", "-C", str(directory), f"-j{b.jobs}"])
        b.env["DESTDIR"] = str(b.stage)
        b.run([str(cmake), "--install", str(directory)])
        b.license(name, ["COPYING"])
        b.probe(b.stage / "usr/bin/pcre2grep", "--version")
    elif name == "lua":
        source = b.source / "src"
        b.run(["make", f"-j{b.jobs}", "linux-noreadline", f"CC={b.wrapper}",
               f"AR={b.env['AR']} rcu", f"RANLIB={b.env['RANLIB']}",
               "MYCFLAGS=-fPIC", "MYLDFLAGS=-static-libgcc"], source)
        library = b.stage / "usr/lib"
        library.mkdir(parents=True, exist_ok=True)
        objects = [str(p) for p in sorted(source.glob("*.o")) if p.name not in {"lua.o", "luac.o"}]
        b.run([str(b.wrapper), "-shared", "-static-libgcc", "-Wl,-soname,liblua5.4.so.0",
               "-o", str(library / "liblua5.4.so.0.0.0"), *objects, "-lm", "-ldl"])
        for suffix in ("liblua5.4.so", "liblua5.4.so.0"):
            (library / suffix).symlink_to("liblua5.4.so.0.0.0")
        include = b.stage / "usr/include/lua5.4"
        include.mkdir(parents=True)
        for file in ("lua.h", "luaconf.h", "lualib.h", "lauxlib.h"):
            shutil.copy2(source / file, include / file)
        binary = b.stage / "usr/bin"
        binary.mkdir()
        for file in ("lua", "luac"):
            shutil.copy2(source / file, binary / file)
        pc = library / "pkgconfig"
        pc.mkdir()
        text = "prefix=/usr\nlibdir=${prefix}/lib\nincludedir=${prefix}/include/lua5.4\n\nName: Lua\nDescription: Lua language runtime\nVersion: 5.4.9\nLibs: -L${libdir} -llua5.4\nLibs.private: -lm -ldl\nCflags: -I${includedir}\n"
        for file in ("lua.pc", "lua5.4.pc", "lua-5.4.pc"):
            (pc / file).write_text(text)
        b.license(name, ["doc/readme.html"])
        b.probe(binary / "lua", "-e", "assert(2+2==4); print('CUSTOM_LUA_OK')")
    elif name == "duktape":
        b.run(["make", "-f", "Makefile.sharedlibrary", f"-j{b.jobs}", f"CC={b.wrapper}",
               "INSTALL_PREFIX=/usr", "LIBDIR=/lib", "LDFLAGS=-static-libgcc -lm"])
        b.run(["make", "-f", "Makefile.sharedlibrary", "install", f"CC={b.wrapper}",
               "INSTALL_PREFIX=/usr", "LIBDIR=/lib", f"DESTDIR={b.stage}"])
        for path in (b.stage / "usr/lib").glob("libduktaped*"):
            path.unlink()
        b.license(name, ["LICENSE.txt"])
    elif name == "glib":
        b.meson(["-Dtests=false", "-Dinstalled_tests=false", "-Dintrospection=disabled",
                 "-Dnls=disabled", "-Dman-pages=disabled", "-Ddocumentation=false",
                 "-Dselinux=disabled", "-Dlibmount=enabled", "-Dsysprof=disabled",
                 "-Ddtrace=disabled", "-Dsystemtap=disabled", "-Dlibelf=disabled",
                 "-Dglib_debug=disabled"])
        b.license(name, ["COPYING"])
        shutil.copytree(b.source / "LICENSES", b.stage / "usr/share/licenses/glib/LICENSES")
        sdk = b.stage / "usr/share/glib-2.0/sdk-tools"
        sdk.mkdir(parents=True, exist_ok=True)
        for file in ("gdbus-codegen", "glib-mkenums", "glib-genmarshal", "gtester-report"):
            path = b.stage / "usr/bin" / file
            if path.is_file():
                path.rename(sdk / file)
        # Source Python generators stay SDK data until target Python is built.
        (b.stage / "usr/bin/glib-gettextize").unlink(missing_ok=True)
        b.probe(b.stage / "usr/bin/glib-compile-resources", "--version")
    elif name == "libgudev":
        b.glib_generators()
        b.meson(["-Dintrospection=disabled", "-Dtests=disabled", "-Dvapi=disabled", "-Dgtk_doc=false"])
        b.license(name, ["COPYING"])
    elif name == "wireplumber":
        b.glib_generators()
        b.gettext_generators()
        b.meson(["-Dintrospection=disabled", "-Ddoc=disabled", "-Dtests=false", "-Ddbus-tests=false",
                 "-Dsystem-lua=true", "-Dsystem-lua-version=5.4", "-Dsystemd=enabled",
                 "-Delogind=disabled", "-Dsystemd-user-unit-dir=/usr/lib/systemd/user",
                 "-Dsystemd-system-unit-dir=/usr/lib/systemd/system"])
        b.license(name, ["LICENSE"])
        b.probe(b.stage / "usr/bin/wireplumber", "--version")
    elif name == "polkit":
        b.glib_generators()
        b.gettext_generators()
        b.meson(["-Dintrospection=false", "-Dgtk_doc=false", "-Dman=false", "-Dtests=false",
                 "-Dexamples=false", "-Dgettext=true", "-Dsession_tracking=logind", "-Dauthfw=pam",
                 "-Dos_type=lfs", "-Dprivileged_group=wheel", "-Dpam_include=system-auth",
                 "-Dpam_prefix=/etc/pam.d", "-Dpolkitd_user=polkitd",
                 "-Dsystemdsystemunitdir=/usr/lib/systemd/system"])
        # Upstream's install helper asks non-root packagers to set these modes.
        # Files remain private; ALPM assigns root ownership during guest install.
        for file in ("usr/bin/pkexec", "usr/lib/polkit-1/polkit-agent-helper-1"):
            (b.stage / file).chmod(0o4755)
        b.license(name, ["COPYING"])
        b.probe(b.stage / "usr/bin/pkcheck", "--version")
    elif name == "upower":
        b.glib_generators()
        b.gettext_generators()
        b.meson(["-Dman=false", "-Dgtk-doc=false", "-Dintrospection=disabled", "-Dinstalled_tests=false",
                 "-Didevice=disabled", "-Dpolkit=enabled", "-Dos_backend=linux", "-Dzshcompletiondir=no",
                 "-Dudevrulesdir=/usr/lib/udev/rules.d", "-Dudevhwdbdir=/usr/lib/udev/hwdb.d",
                 "-Dhistorydir=/var/lib/upower", "-Dstatedir=/var/lib/upower",
                 "-Dsystemdsystemunitdir=/usr/lib/systemd/system"])
        b.license(name, ["COPYING"])
        # --version queries a running system-bus daemon. --help exercises the
        # compiled CLI without contacting the host's system bus.
        b.closed_probe(b.stage / "usr/bin/upower", "--help")
    elif name == "power-profiles-daemon":
        b.glib_generators()
        b.gettext_generators()
        b.meson(["-Dtests=false", "-Dpylint=disabled", "-Dmanpage=disabled", "-Dbashcomp=disabled",
                 "-Dgtk_doc=false", "-Dsystemdsystemunitdir=/usr/lib/systemd/system"])
        # Settings talks directly to the packaged D-Bus daemon. The optional
        # upstream Python/GI CLI needs a separately packaged Python SDK first.
        (b.stage / "usr/bin/powerprofilesctl").unlink(missing_ok=True)
        b.license(name, ["COPYING"])
    elif name == "libpng":
        cmake = Path(shutil.which("cmake"))
        (b.work / "cmake-seed.json").write_text(json.dumps({"kind": "declared-host-build-generator",
            "path": str(cmake), "sha256": hashlib.sha256(cmake.read_bytes()).hexdigest()}) + "\n")
        directory = b.work / "target-build"
        b.run([str(cmake), "-S", str(b.source), "-B", str(directory), "-G", "Ninja",
            "-DCMAKE_SYSTEM_NAME=Linux", f"-DCMAKE_C_COMPILER={b.wrapper}", f"-DCMAKE_SYSROOT={b.sysroot}",
            f"-DCMAKE_FIND_ROOT_PATH={b.sysroot}", "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY",
            "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_INSTALL_PREFIX=/usr", "-DCMAKE_INSTALL_LIBDIR=lib", "-DCMAKE_SKIP_RPATH=ON",
            "-DPNG_SHARED=ON", "-DPNG_STATIC=OFF", "-DPNG_TESTS=OFF", "-DPNG_TOOLS=OFF"])
        b.run(["ninja", "-C", str(directory), f"-j{b.jobs}"])
        b.env["DESTDIR"] = str(b.stage)
        b.run([str(cmake), "--install", str(directory)])
        b.license(name, ["LICENSE"])
    elif name == "json-glib":
        b.glib_generators()
        b.meson(["-Dintrospection=disabled", "-Ddocumentation=disabled", "-Dman=false", "-Dtests=false",
                 "-Dconformance=false", "-Dinstalled_tests=false", "-Dnls=disabled"])
        b.license(name, ["COPYING"])
        shutil.copytree(b.source / "LICENSES", b.stage / "usr/share/licenses/json-glib/LICENSES")
    elif name == "gdk-pixbuf":
        b.glib_generators()
        b.gettext_generators()
        b.meson(["-Dintrospection=disabled", "-Ddocumentation=false", "-Dman=false", "-Dtests=false",
                 "-Dinstalled_tests=false", "-Dthumbnailer=disabled", "-Dpng=enabled", "-Dbuiltin_loaders=png",
                 "-Dtiff=disabled", "-Djpeg=disabled", "-Dgif=disabled", "-Dglycin=disabled",
                 "-Dandroid=disabled", "-Dothers=disabled", "-Dlegacy_xpm=disabled"])
        b.license(name, ["COPYING"])
    elif name == "wl-clipboard":
        b.scanner()
        b.meson(["-Dprotocols=enabled", "-Dzshcompletiondir=no", "-Dfishcompletiondir=no"])
        b.license(name, ["COPYING"])
        b.closed_probe(b.stage / "usr/bin/wl-copy", "--version")
    elif name == "xdg-utils":
        source = b.source / "scripts"
        binary = b.stage / "usr/bin"
        binary.mkdir(parents=True, exist_ok=True)
        for program in ("xdg-desktop-menu", "xdg-desktop-icon", "xdg-mime", "xdg-icon-resource",
                        "xdg-open", "xdg-email", "xdg-screensaver", "xdg-settings"):
            # The official source tag carries DocBook XML, but no pre-rendered
            # .txt help files. Render those inputs with declared host Python;
            # upstream's AWK still assembles the actual dispatch implementation.
            document = ET.parse(source / "desc" / f"{program}.xml").getroot()
            flatten = lambda node: " ".join("".join(node.itertext()).split())
            named = document.find("refnamediv")
            parts = ["Name", "", flatten(named), "", "Synopsis", ""]
            parts += [flatten(node) for node in document.findall("refsynopsisdiv/cmdsynopsis")]
            for section in document.findall("refsect1"):
                parts += ["", flatten(section.find("title")), ""]
                parts += [textwrap.fill(flatten(node), width=80) for node in section if node.tag != "title"]
            (source / f"{program}.txt").write_text("\n".join(parts) + "\n")
            content = subprocess.check_output(["awk", "-f", "generate-help-script.awk", f"{program}.in"],
                                              cwd=source, env=b.env, text=True).replace("@NAME@", program)
            target = binary / program
            target.write_text(content)
            target.chmod(0o755)
            subprocess.run(["/bin/sh", "-n", str(target)], check=True)
        b.license(name, ["LICENSE"])
        (b.work / "script-profile.json").write_text(json.dumps({"dispatch": "upstream shell implementation",
            "help": "stdlib XML text rendering then upstream AWK assembly", "native_seed": "Python, AWK, shell"}) + "\n")
        b.audit_payload_paths()
        return
    elif name in {"gstreamer", "gst-plugins-base", "gst-plugins-good"}:
        b.glib_generators()
        options = ["-Dauto_features=disabled", "-Ddoc=disabled", "-Dtests=disabled", "-Dexamples=disabled",
                   "-Dnls=disabled"]
        if name == "gstreamer":
            options += ["-Dtools=enabled", "-Dbenchmarks=disabled", "-Dintrospection=disabled",
                        "-Dcheck=disabled", "-Dptp-helper=disabled", "-Dgst_parse=true"]
        elif name == "gst-plugins-base":
            options += ["-Dintrospection=disabled", "-Dorc=disabled", "-Dtools=enabled",
                        *[f"-D{plugin}=enabled" for plugin in
                          ("app", "audioconvert", "audioresample", "gio", "pbtypes", "playback", "rawparse", "typefind", "volume")]]
        else:
            options += ["-Dorc=disabled", "-Dwavparse=enabled", "-Dwavenc=enabled",
                        "-Dauparse=enabled", "-Daudioparsers=enabled"]
        b.meson(options)
        b.license(name, ["COPYING"])
    elif name == "xdg-desktop-portal":
        b.glib_generators()
        b.gettext_generators()
        # Do not probe the host plugin registry when detecting WAV support.
        program = b.native / "bin/gst-inspect-1.0"
        program.write_text("#!/usr/bin/python3\nimport os,sys\n"
            f"loader={str(b.sysroot / 'usr/lib/ld-linux-x86-64.so.2')!r}\n"
            f"os.environ['GST_PLUGIN_SYSTEM_PATH_1_0']={str(b.sysroot / 'usr/lib/gstreamer-1.0')!r}\n"
            "os.environ['GST_REGISTRY_FORK']='no'\n"
            f"os.execv(loader,[loader,'--library-path',{str(b.sysroot / 'usr/lib')!r},{str(b.sysroot / 'usr/bin/gst-inspect-1.0')!r},*sys.argv[1:]])\n")
        program.chmod(0o755)
        b.env["GST_REGISTRY_1_0"] = str(b.work / "gst-registry.bin")
        b.closed_loader(b.sysroot / "usr/bin/gst-inspect-1.0")
        b.run([str(program), "wavparse", "--exists"], b.work)
        b.meson(["-Dtests=disabled", "-Dinstalled-tests=false", "-Ddocumentation=disabled", "-Dman-pages=disabled",
                 "-Dflatpak-interfaces=disabled", "-Dgeoclue=disabled", "-Dgudev=enabled", "-Dsystemd=enabled",
                 "-Dsystemd-user-unit-dir=/usr/lib/systemd/user", "-Dsandboxed-image-validation=enabled",
                 "-Dsandboxed-sound-validation=enabled"])
        b.license(name, ["COPYING"])
    elif name == "bubblewrap":
        b.meson(["-Dsupport_setuid=false", "-Dselinux=disabled", "-Dtests=false",
                 "-Dman=disabled", "-Dbash_completion=disabled", "-Dzsh_completion=disabled"])
        b.license(name, ["COPYING"])
        b.probe(b.stage / "usr/bin/bwrap", "--version")
    elif name == "fuse3":
        b.meson(["-Dtests=false", "-Dexamples=false", "-Duseroot=false",
                 "-Denable-io-uring=false", "-Denable-custom-io=false", "-Ddisable-mtab=true",
                 "-Dudevrulesdir=/usr/lib/udev/rules.d", "-Dinitscriptdir="])
        # Upstream normally applies this at root install time. Stage it without
        # host chown; ALPM assigns guest root ownership. The document portal
        # needs the constrained helper to mount FUSE for a normal desktop user.
        (b.stage / "usr/bin/fusermount3").chmod(0o4755)
        b.license(name, ["LICENSE"])
        b.probe(b.stage / "usr/bin/fusermount3", "--version")
    else:
        raise RuntimeError(f"Desktop service adapter not implemented: {name}")
    for path in b.stage.rglob("*.la"):
        path.unlink()
    b.audit()
    b.audit_payload_paths()
