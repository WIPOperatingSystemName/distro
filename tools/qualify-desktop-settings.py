#!/usr/bin/env python3
"""Qualify real target dconf/GSettings reads and user writes on a private bus."""
from pathlib import Path
import hashlib
import json
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))
from distro_build.desktop_native import _link_wrapper
from distro_build.graph import plan
from distro_build.model import catalog
from distro_build.packaging import PacmanToolkit

digest = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
records = [json.loads((project / "out/state/packages" / (recipe.name + ".json")).read_text())
           for recipe in plan(catalog(project), ["dconf", "gsettings-desktop-schemas"])]
identity = hashlib.sha256(json.dumps({"packages": {r["package"]: r["sha256"] for r in records},
                                     "qualifier": digest(__file__)}, sort_keys=True).encode()).hexdigest()
work = project / "out/qualification/desktop-settings" / identity
work.mkdir(parents=True, exist_ok=True)
root = work / "sysroot"
if root.exists():
    shutil.rmtree(root)
PacmanToolkit(project / "out/native-toolkit/prefix").install(root, [Path(r["path"]) for r in records],
    bootstrap=True, expected_hashes={Path(r["path"]).name: r["sha256"] for r in records})
bootstrap = project / "out/bootstrap/root"
shutil.copytree(bootstrap / "usr/include", root / "usr/include", symlinks=True, dirs_exist_ok=True)
tools = bootstrap / "tools"
compiler = tools / "pass2/bin/x86_64-custom-linux-gnu-gcc"
wrapper = work / "target-cc"
_link_wrapper(wrapper, compiler, root, [root, tools, work])
env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
       "PKG_CONFIG_SYSROOT_DIR": str(root), "PKG_CONFIG_PATH": "",
       "PKG_CONFIG_LIBDIR": str(root / "usr/lib/pkgconfig") + ":" + str(root / "usr/share/pkgconfig"),
       "GIO_MODULE_DIR": str(root / "usr/lib/gio/modules"), "GIO_USE_VFS": "local",
       "GSETTINGS_BACKEND": "dconf", "GSETTINGS_SCHEMA_DIR": str(root / "usr/share/glib-2.0/schemas")}
loader = root / "usr/lib/ld-linux-x86-64.so.2"
def command(binary):
    prefix = [str(loader), "--inhibit-cache", "--library-path", str(root / "usr/lib")]
    listing = subprocess.check_output([*prefix, "--list", str(binary)], env=env, text=True)
    for line in listing.splitlines():
        if "linux-vdso" in line:
            continue
        match = re.search(r"(?:=>\s*)?(/[^\s]+)\s+\(0x[0-9a-f]+\)", line)
        if not match or not Path(match[1]).resolve().is_relative_to(root):
            raise RuntimeError("Target loader resolved outside its package root: " + line)
    (work / (binary.name + "-loader-list.txt")).write_text(listing)
    return [*prefix, str(binary)]

source = work / "probe.c"
source.write_text(r'''
#define _GNU_SOURCE
#include <gio/gio.h>
#include <link.h>
#include <stdio.h>
#include <string.h>
static int paths(struct dl_phdr_info *i,size_t n,void *p){(void)n;(void)p;if(i->dlpi_name[0])printf("LOADED %s\n",i->dlpi_name);return 0;}
int main(int argc,char **argv){
 if(argc!=3)return 2;
 GSettings *s=g_settings_new("org.gnome.desktop.wm.preferences");
 if(!strcmp(argv[1],"set")){if(!g_settings_set_string(s,"button-layout",argv[2]))return 3;g_settings_sync();}
 char *value=g_settings_get_string(s,"button-layout");
 if(strcmp(value,argv[2])){fprintf(stderr,"Unexpected settings value: %s\n",value);return 4;}
 printf("VALUE %s\n",value);g_free(value);dl_iterate_phdr(paths,NULL);g_object_unref(s);
 puts("CUSTOM_TARGET_GSETTINGS_DCONF_OK");return 0;
}
''')
cflags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "gio-2.0"], env=env, text=True))
libs = shlex.split(subprocess.check_output(["pkg-config", "--libs", "gio-2.0"], env=env, text=True))
binary = work / "probe"
obj = work / "probe.o"
subprocess.run([str(wrapper), "-c", str(source), "-o", str(obj), *cflags], env=env, check=True)
link = subprocess.run([str(wrapper), "-static-libgcc", "-Wl,--verbose", str(obj), *libs,
                       "-ldl", "-o", str(binary)], env=env, capture_output=True, text=True, check=True)
(work / "link.log").write_text(link.stdout + link.stderr)
for value in re.findall(r"attempt to open (.+) succeeded", link.stdout):
    path = Path(value)
    if path.is_absolute() and not any(path.resolve().is_relative_to(p.resolve()) for p in (root, tools, work)):
        raise RuntimeError("Outside target linker input: " + value)
results = []
loaded = {}
with tempfile.TemporaryDirectory(prefix="custom-dconf-", dir="/tmp") as directory:
    private = Path(directory)
    sources = private / "defaults.d"
    sources.mkdir()
    (sources / "desktop").write_text("[org/gnome/desktop/wm/preferences]\nbutton-layout='appmenu:minimize,maximize,close'\n")
    database = private / "defaults"
    subprocess.run([*command(root / "usr/bin/dconf"), "compile", str(database), str(sources)], env=env, check=True)
    profile = private / "profile"
    profile.write_text("user-db:user\nfile-db:" + str(database) + "\n")
    env.update({"HOME": str(private), "XDG_CONFIG_HOME": str(private / "config"),
                "XDG_RUNTIME_DIR": str(private), "DCONF_PROFILE": str(profile)})
    config = work / "bus.conf"
    config.write_text('<busconfig><type>session</type><listen>unix:path=' + str(private / "bus") + '</listen><auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
    with (work / "bus.log").open("w") as buslog, (work / "dconf-service.log").open("w") as servicelog:
        bus = subprocess.Popen([*command(root / "usr/bin/dbus-daemon"), "--nofork", "--print-address",
                                "--config-file=" + str(config)], env=env, stdout=subprocess.PIPE,
                               stderr=buslog, text=True)
        service = None
        try:
            if not select.select([bus.stdout], [], [], 5)[0]:
                raise RuntimeError("Private target D-Bus daemon did not become ready")
            address = bus.stdout.readline().strip()
            if not address.startswith("unix:path=" + str(private / "bus")):
                raise RuntimeError("Unexpected private bus address: " + address)
            env["DBUS_SESSION_BUS_ADDRESS"] = address
            service = subprocess.Popen(command(root / "usr/libexec/dconf-service"), env=env,
                                       stdout=servicelog, stderr=servicelog)
            client = command(root / "usr/bin/dbus-send")
            deadline = time.monotonic() + 5
            while True:
                if service.poll() is not None:
                    raise RuntimeError("Target dconf service exited before owning its bus name")
                names = subprocess.check_output([*client, "--session", "--print-reply", "--type=method_call",
                    "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.ListNames"],
                    env=env, text=True, timeout=3)
                if 'string "ca.desrt.dconf"' in names:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Target dconf service did not claim ca.desrt.dconf")
                time.sleep(0.1)
            for mode, value in (("read", "appmenu:minimize,maximize,close"), ("set", ":close"), ("read", ":close")):
                result = subprocess.run([*command(binary), mode, value], env=env, capture_output=True,
                                        text=True, check=True, timeout=10)
                results.append({"mode": mode, "expected": value, "stdout": result.stdout, "stderr": result.stderr})
                if "CUSTOM_TARGET_GSETTINGS_DCONF_OK" not in result.stdout:
                    raise RuntimeError("Missing target settings API success marker")
                module_present = False
                for line in result.stdout.splitlines():
                    if not line.startswith("LOADED "):
                        continue
                    value = line.removeprefix("LOADED ")
                    if value == "linux-vdso.so.1":
                        continue
                    path = Path(value).resolve()
                    if not path.is_relative_to(root):
                        raise RuntimeError("Target loaded a library outside the private package root: " + value)
                    module_present |= path.name == "libdconfsettings.so"
                    loaded[str(path.relative_to(root))] = digest(path)
                if not module_present:
                    raise RuntimeError("GSettings did not load the actual packaged dconf backend")
            user_db = private / "config/dconf/user"
            if not user_db.is_file():
                raise RuntimeError("The real user service did not persist the override database")
            database_hash, user_hash = digest(database), digest(user_db)
        finally:
            for process in (service, bus):
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
report = {"success": True, "identity": identity, "scope": "source-built target dconf compiler, complete desktop schemas, actual GIO dconf plugin, private EXTERNAL-authenticated user service and persisted user override",
          "packages": records, "database_sha256": database_hash, "user_database_sha256": user_hash,
          "loaded": loaded, "results": results, "qualifier_sha256": digest(__file__),
          "source_sha256": digest(source), "compiler_sha256": digest(compiler)}
(work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"success": True, "report": str(work / "report.json")}, indent=2))
