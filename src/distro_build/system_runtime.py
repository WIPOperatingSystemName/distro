"""Source-built authentication, mount and systemd session runtime.

Recipes stage packages only. They never activate host services, create accounts,
or replace the console image's init process. Service/session policy belongs to
the selected image profile and must be tested in QEMU separately.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from .desktop_native import DesktopBuild


class SystemBuild(DesktopBuild):
    def require_gperf(self) -> None:
        project = Path(os.environ["CD_BOOTSTRAP_ROOT"]).parents[2]
        self.run(["python3", "-B", str(project / "bootstrap/run-stage.py"),
                  "--sources", str(project / "out/sources/downloads"),
                  "--work", str(project / "out/bootstrap/work"),
                  "--root", str(project / "out/bootstrap/root"),
                  "--tools", str(self.tools), "--check", "--stage", "gperf-native"], self.work)

    def compile_probe(self, name: str, text: str, libraries: list[str], *arguments: str) -> str:
        source = self.work / f"{name}.c"
        source.write_text(text)
        binary = self.work / name
        self.run([str(self.wrapper), "-O2", "-static-libgcc", str(source),
                  "-I" + str(self.stage / "usr/include"), "-L" + str(self.stage / "usr/lib"),
                  *["-l" + library for library in libraries], "-o", str(binary)], self.work)
        loader = self.sysroot / "usr/lib/ld-linux-x86-64.so.2"
        result = subprocess.run([str(loader), "--library-path",
                                 f"{self.stage}/usr/lib:{self.sysroot}/usr/lib", str(binary), *arguments],
                                env=self.env, text=True, capture_output=True, check=True)
        print(result.stdout, end="", flush=True)
        return result.stdout.strip()

    def audit_systemd(self) -> None:
        # Upstream installs its private shared library in /usr/lib/systemd and
        # deliberately records exactly that guest directory as a run path.
        # Permit that owned directory only; reject every build/host search path.
        provided = set()
        for root in (self.stage, self.sysroot):
            for directory in ("usr/lib", "usr/lib/systemd", "lib", "lib64"):
                folder = root / directory
                if folder.is_dir():
                    provided.update(path.name for path in folder.iterdir() if path.is_file())
        audited = []
        for path in sorted(self.stage.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
            dynamic = subprocess.check_output([str(self.readelf), "-d", str(path)], text=True)
            program = subprocess.check_output([str(self.readelf), "-l", str(path)], text=True)
            runpaths = re.findall(r"\((?:RPATH|RUNPATH)\).*?\[([^]]+)\]", dynamic)
            if any(entry != "/usr/lib/systemd" for field in runpaths for entry in field.split(":")):
                raise RuntimeError(f"unexpected systemd target run path: {path}: {runpaths}")
            needed = re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic)
            if set(needed) - provided:
                raise RuntimeError(f"unprovided systemd runtime library: {path}: {set(needed) - provided}")
            interpreters = re.findall(r"Requesting program interpreter: ([^]]+)", program)
            if any(item != "/lib64/ld-linux-x86-64.so.2" for item in interpreters):
                raise RuntimeError(f"unexpected systemd target interpreter: {path}: {interpreters}")
            audited.append({"file": str(path.relative_to(self.stage)), "needed": needed,
                            "runpaths": runpaths, "interpreter": interpreters})
        if not audited:
            raise RuntimeError("systemd package has no target ELF payload")
        (self.work / "target-elf-audit.json").write_text(json.dumps(audited, indent=2) + "\n")


def build_system_runtime(name: str) -> None:
    build = SystemBuild()
    if name == "libxcrypt":
        build.autotools(["--disable-static", "--enable-hashes=strong,glibc", "--enable-obsolete-api=no"])
        build.license(name, ["COPYING.LIB"])
        result = build.compile_probe("crypt-probe", """#include <crypt.h>
#include <stdio.h>
#include <string.h>
int main(void) { struct crypt_data a = {0}, b = {0};
  char *x=crypt_r("custom-distro-test", "$6$rounds=10000$customdistro$", &a);
  char *y=crypt_r("wrong-password", "$6$rounds=10000$customdistro$", &b);
  if (!x || !y || x[0]=='*' || strcmp(x,y)==0) return 1;
  puts("CUSTOM_CRYPT_OK"); return 0; }
""", ["crypt"])
        if result != "CUSTOM_CRYPT_OK":
            raise RuntimeError("target crypt password-hashing probe failed")
    elif name == "libcap":
        build.require_gperf()
        options = ["prefix=/usr", "lib=lib", "PAM_CAP=no", "GOLANG=no", "DYNAMIC=yes",
                   "CC=" + str(build.wrapper), "BUILD_CC=/usr/bin/gcc",
                   "AR=" + build.env["AR"], "RANLIB=" + build.env["RANLIB"],
                   "OBJCOPY=" + str(build.tools / "bin" / f"{build.target}-objcopy"),
                   "LDFLAGS=-static-libgcc"]
        build.run(["make", "-C", "libcap", f"-j{build.jobs}", *options])
        build.run(["make", "-C", "libcap", f"DESTDIR={build.stage}", *options, "install"])
        build.license(name, ["License"])
        result = build.compile_probe("cap-probe", """#include <sys/capability.h>
#include <stdio.h>
int main(void) { cap_t state=cap_get_proc(); if (!state) return 1;
  char *text=cap_to_text(state, 0); if (!text) return 2;
  cap_free(text); cap_free(state); puts("CUSTOM_CAP_OK"); return 0; }
""", ["cap"])
        if result != "CUSTOM_CAP_OK":
            raise RuntimeError("target capabilities API probe failed")
    elif name == "libseccomp":
        build.require_gperf()
        build.autotools(["--disable-static"])
        build.license(name, ["LICENSE"])
        result = build.compile_probe("seccomp-probe", """#include <seccomp.h>
#include <stdio.h>
int main(void) { scmp_filter_ctx f=seccomp_init(SCMP_ACT_KILL_PROCESS);
  if (!f || seccomp_rule_add(f, SCMP_ACT_ALLOW, SCMP_SYS(getpid), 0)<0) return 1;
  seccomp_release(f); puts("CUSTOM_SECCOMP_API_OK"); return 0; }
""", ["seccomp"])
        if result != "CUSTOM_SECCOMP_API_OK":
            raise RuntimeError("target seccomp filter-construction API probe failed")
    elif name == "pam":
        _pam(build)
    elif name == "util-linux":
        programs = ["libuuid", "libblkid", "libmount", "libsmartcols", "libfdisk", "agetty", "login", "su",
                    "sulogin", "unshare", "nsenter", "mount", "swapon", "losetup", "lsblk"]
        build.autotools(["--disable-all-programs", *["--enable-" + program for program in programs],
                         f"--with-sysroot={build.sysroot}",
                         "--bindir=/usr/bin", "--sbindir=/usr/sbin",
                         "--without-systemd", "--without-ncursesw", "--without-readline", "--without-python",
                         "--disable-nls", "--disable-static",
                         "--disable-libmount-udev-support", "--disable-makeinstall-chown", "--disable-makeinstall-setuid"])
        build.license(name, ["COPYING"])
        build.probe(build.stage / "usr/bin/lsblk", "--version")
        if not (build.stage / "usr/bin/login").is_file() or not (build.stage / "usr/sbin/agetty").is_file():
            raise RuntimeError("util-linux login/getty payload was not built")
    elif name == "systemd":
        _systemd(build, polkit=True)
        _remove_public_systemd_library(build.stage)
    elif name == "libsystemd":
        _systemd(build)
        _retain_public_systemd_library(build)
        result = build.compile_probe("libsystemd-probe", """#include <systemd/sd-bus.h>
#include <systemd/sd-daemon.h>
#include <systemd/sd-event.h>
#include <systemd/sd-id128.h>
#include <stdio.h>
int main(void) { sd_bus *bus=0; sd_event *event=0; sd_id128_t id;
  if (sd_bus_new(&bus)<0 || sd_event_new(&event)<0 || sd_id128_randomize(&id)<0) return 1;
  if (sd_listen_fds(0)!=0) return 2;
  sd_bus_unref(bus); sd_event_unref(event); puts("CUSTOM_LIBSYSTEMD_API_OK"); return 0; }
""", ["systemd"])
        if result != "CUSTOM_LIBSYSTEMD_API_OK":
            raise RuntimeError("target libsystemd API construction probe failed")
    else:
        raise RuntimeError(f"unsupported system runtime recipe: {name}")
    for path in build.stage.rglob("*.la"):
        path.unlink()
    if name == "systemd":
        build.audit_systemd()
    else:
        build.audit()


def _pam(build: SystemBuild) -> None:
    build.meson(["-Ddocs=disabled", "-Di18n=disabled", "-Daudit=disabled", "-Deconf=disabled",
                 "-Dselinux=disabled", "-Dnis=disabled", "-Dlogind=disabled", "-Delogind=disabled",
                 "-Dopenssl=disabled", "-Dpwaccess=disabled", "-Dpam_userdb=disabled",
                 "-Dpam_unix=enabled", "-Dexamples=false", "-Dxtests=false",
                 "-Dsecuredir=/usr/lib/security", "-Dsconfigdir=/etc/security", "-Dvendordir=/usr/share/pam"])
    build.license("pam", ["Copyright"])
    # The source helper must read root-owned shadow credentials when an
    # unprivileged PAM client invokes it. Package ownership is encoded as root
    # by the exporter; the staging file remains owned by the build user.
    helper = build.stage / "usr/sbin/unix_chkpwd"
    if not helper.is_file():
        raise RuntimeError("PAM did not build its shadow-password helper")
    helper.chmod(0o4755)
    policy = build.stage / "etc/pam.d/other"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("# Deny services without an explicitly configured authentication policy.\n"
                      "auth required pam_deny.so\naccount required pam_deny.so\n"
                      "password required pam_deny.so\nsession required pam_deny.so\n")
    fixture = build.work / "pam-fixture"
    fixture.mkdir()
    for service, module in [("distro-deny", "pam_deny.so"), ("distro-allow-fixture", "pam_permit.so")]:
        (fixture / service).write_text("auth required " + str(build.stage / "usr/lib/security" / module) + "\n")
    result = build.compile_probe("pam-probe", """#include <security/pam_appl.h>
#include <stdio.h>
static int conversation(int n, const struct pam_message **m, struct pam_response **r, void *d) {
  (void)n; (void)m; (void)r; (void)d; return PAM_CONV_ERR; }
int main(int n,char **a) { struct pam_conv c={conversation,0}; pam_handle_t *p=0;
  if(n!=2 || pam_start_confdir("distro-deny","nobody",&c,a[1],&p)!=PAM_SUCCESS) return 1;
  int status=pam_authenticate(p,0); pam_end(p,status); if(status!=PAM_AUTH_ERR) return 2;
  if(pam_start_confdir("distro-allow-fixture","nobody",&c,a[1],&p)!=PAM_SUCCESS) return 3;
  status=pam_authenticate(p,0); pam_end(p,status); if(status!=PAM_SUCCESS) return 4;
  puts("CUSTOM_PAM_MODULES_OK"); return 0; }
""", ["pam"], str(fixture))
    if result != "CUSTOM_PAM_MODULES_OK":
        raise RuntimeError("target PAM module execution/deny-policy probe failed")


def _systemd(build: SystemBuild, *, polkit: bool = False) -> None:
    build.require_gperf()
    generators = {}
    for module in ("jinja2", "markupsafe"):
        spec = importlib.util.find_spec(module)
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError(f"systemd's declared native Python generator requires {module}")
        files = sorted(path for directory in spec.submodule_search_locations
                       for path in Path(directory).rglob("*")
                       if path.is_file() and path.suffix in {".py", ".so"})
        generators[module] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    (build.work / "native-generator-inputs.json").write_text(json.dumps(generators, indent=2) + "\n")
    disabled_features = ["acl", "audit", "kmod", "xenctrl", "passwdqc", "pwquality", "microhttpd",
                         "libcryptsetup", "libcryptsetup-plugins", "libcurl", "libidn2", "libidn",
                         "qrencode", "gcrypt", "gnutls", "openssl", "p11kit", "libfido2", "tpm2",
                         "elfutils", "zlib", "bzip2", "xz", "lz4", "zstd", "xkbcommon", "pcre2",
                         "glib", "dbus", "libarchive", "selinux", "apparmor", "bootloader",
                         "ukify", "homed", "remote", "nss-mymachines", "nss-resolve", "vmspawn",
                         "importd", "nspawn", "repart", "sysupdate", "man", "html", "bpf-framework"]
    disabled_bools = ["efi", "tpm", "hibernate", "coredump", "pstore", "oomd", "localed", "machined",
                      "portabled", "sysext", "mountfsd", "userdb", "networkd", "resolve", "timesyncd",
                      "backlight", "vconsole", "storagetm", "rfkill", "kernel-install", "translations", "utmp",
                      "ldconfig", "quotacheck", "binfmt", "nsresourced"]
    build.meson(["-Dmode=release", "-Dversion-tag=259.9", "-Dvcs-tag=false", "-Dtests=false",
                 "-Dslow-tests=false", "-Dfuzz-tests=false", "-Dinstall-tests=false", "-Dsplit-bin=false",
                 "-Dlibmount=enabled", "-Dblkid=enabled", "-Dfdisk=enabled", "-Dpam=enabled", "-Dseccomp=enabled",
                 "-Dlogind=true", "-Dsysusers=true", "-Dtmpfiles=true", "-Dhwdb=true",
                 "-Dpolkit=" + ("enabled" if polkit else "disabled"),
                 "-Dsulogin-path=/usr/sbin/sulogin", "-Dagetty-path=/usr/sbin/agetty", "-Dmount-path=/usr/bin/mount",
                 "-Ddefault-user-shell=/bin/sh", "-Dnologin-path=/bin/false",
                 "-Dumount-path=/usr/bin/umount", "-Dpamlibdir=/usr/lib/security", "-Dpamconfdir=/etc/pam.d",
                 *["-D" + option + "=disabled" for option in disabled_features],
                 *["-D" + option + "=false" for option in disabled_bools]])
    build.license("systemd", ["LICENSE.LGPL2.1", "LICENSE.GPL2"])
    shutil.copytree(build.source / "LICENSES", build.stage / "usr/share/licenses/systemd/LICENSES")
    expected = ["usr/lib/systemd/systemd", "usr/lib/systemd/systemd-logind", "usr/lib/systemd/systemd-udevd",
                "usr/lib/security/pam_systemd.so", "usr/lib/libsystemd.so.0", "usr/lib/libudev.so.1"]
    if any(not (build.stage / item).is_file() for item in expected):
        raise RuntimeError("systemd did not install the required service/udev/logind/PAM payload")
    loader = build.sysroot / "usr/lib/ld-linux-x86-64.so.2"
    result = subprocess.check_output([str(loader), "--library-path",
                f"{build.stage}/usr/lib/systemd:{build.stage}/usr/lib:{build.sysroot}/usr/lib",
                str(build.stage / "usr/lib/systemd/systemd"), "--version"], env=build.env, text=True)
    print(result, end="", flush=True)
    if "systemd 259" not in result or "+PAM" not in result or "+SECCOMP" not in result:
        raise RuntimeError("systemd own-loader version/feature probe failed")
    if polkit:
        _verify_systemd_polkit(build)


def _verify_systemd_polkit(build: SystemBuild) -> None:
    configuration = (build.work / "target-build/config.h").read_text()
    if not re.search(r"^#define ENABLE_POLKIT 1$", configuration, re.M):
        raise RuntimeError("systemd did not compile its genuine polkit protocol integration")
    directory = build.stage / "usr/share/polkit-1/actions"
    manager_policy = directory / "org.freedesktop.systemd1.policy"
    login_policy = directory / "org.freedesktop.login1.policy"
    if not manager_policy.is_file() or not login_policy.is_file():
        raise RuntimeError("systemd polkit policies are missing from the guest policy directory")
    action = ET.parse(manager_policy).getroot().find("action[@id='org.freedesktop.systemd1.manage-units']")
    if action is None:
        raise RuntimeError("systemd upstream manage-units action is missing")
    defaults = {key: action.findtext("defaults/" + key) for key in ("allow_any", "allow_inactive", "allow_active")}
    if defaults != {"allow_any": "auth_admin", "allow_inactive": "auth_admin", "allow_active": "auth_admin_keep"}:
        raise RuntimeError("systemd manage-units policy no longer requires upstream administrator authentication")
    policies = {}
    for path in directory.glob("*.policy"):
        payload = path.read_bytes()
        if str(build.work).encode() in payload or str(build.stage).encode() in payload:
            raise RuntimeError(f"systemd polkit policy contains a private build path: {path}")
        ET.fromstring(payload)
        policies[str(path.relative_to(build.stage))] = hashlib.sha256(payload).hexdigest()
    (build.work / "polkit-feature.json").write_text(json.dumps({"enabled": True,
        "policy_directory": "/usr/share/polkit-1/actions", "manage_units_defaults": defaults,
        "policies": policies, "scope": "compiled integration and unchanged upstream authorization defaults; guest authorization tested separately"}, indent=2) + "\n")
    print("CUSTOM_SYSTEMD_POLKIT_CONFIG_POLICY_OK", flush=True)


def _public_systemd_library_paths(stage: Path) -> set[Path]:
    return {
        *stage.glob("usr/lib/libsystemd.so*"),
        *[path for path in (stage / "usr/include/systemd").rglob("*") if path.is_file() or path.is_symlink()],
        stage / "usr/lib/pkgconfig/libsystemd.pc",
    }


def _remove_public_systemd_library(stage: Path) -> None:
    """The independent libsystemd package owns public library/SDK files."""
    for path in _public_systemd_library_paths(stage):
        path.unlink()
    (stage / "usr/include/systemd").rmdir()


def _retain_public_systemd_library(build: SystemBuild) -> None:
    """Split the freshly compiled public API from the service manager payload.

    Both recipes compile their pinned upstream source through the same declared
    build. No canonical systemd package, host library or existing stage is used
    as an undeclared input to the independent library package.
    """
    license_dir = build.stage / "usr/share/licenses/libsystemd"
    shutil.move(str(build.stage / "usr/share/licenses/systemd"), license_dir)
    retained = _public_systemd_library_paths(build.stage)
    retained.update(path for path in license_dir.rglob("*") if path.is_file())
    for path in sorted(build.stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink() or path.is_file():
            if path not in retained:
                path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for required in ("usr/lib/libsystemd.so.0", "usr/lib/pkgconfig/libsystemd.pc", "usr/include/systemd/sd-daemon.h"):
        if not (build.stage / required).is_file():
            raise RuntimeError(f"independent libsystemd payload missing: {required}")
