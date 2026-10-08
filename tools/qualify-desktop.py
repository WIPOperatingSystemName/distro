#!/usr/bin/env python3
"""Execute basic desktop APIs through only the source-built target loader/libs.

This checks the native library closure and XKB package data. It deliberately does
not claim DRM/input hardware, a compositor session or portal runtime testing.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))
from distro_build.graph import plan
from distro_build.model import catalog
from distro_build.packaging import PacmanToolkit
from distro_build.desktop_native import _link_wrapper

recipes = catalog(project)
names = ["wayland", "wayland-protocols", "libxkbcommon", "libinput", "libdrm", "libseat"]
if "--gbm" in sys.argv:
    names.append("mesa-gbm")
records = [json.loads((project / "out/state/packages" / f"{r.name}.json").read_text())
           for r in plan(recipes, names)]
qualifier_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
identity = hashlib.sha256(json.dumps({"packages": {r["package"]: r["sha256"] for r in records}, "qualifier": qualifier_sha256}, sort_keys=True).encode()).hexdigest()
work = project / "out/qualification/desktop-native" / identity
work.mkdir(parents=True, exist_ok=True)
root = work / "sysroot"
if root.exists():
    shutil.rmtree(root)
packages = [Path(r["path"]) for r in records]
PacmanToolkit(project / "out/native-toolkit/prefix").install(root, packages, bootstrap=True,
    expected_hashes={Path(r["path"]).name: r["sha256"] for r in records})
bootstrap = project / "out/bootstrap/root"
shutil.copytree(bootstrap / "usr/include", root / "usr/include", symlinks=True, dirs_exist_ok=True)
tools = bootstrap / "tools"
compiler = tools / "bin/x86_64-custom-linux-gnu-gcc"
wrapper = work / "target-cc"
_link_wrapper(wrapper, compiler, root, [root, tools, work])
source = work / "probe.c"
source.write_text(r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <link.h>
#include <stdio.h>
#include <wayland-server-core.h>
#include <xkbcommon/xkbcommon.h>
#include <libinput.h>
#include <libudev.h>
static int open_restricted(const char *p, int f, void *d) { (void)p; (void)f; (void)d; return -EACCES; }
static void close_restricted(int fd, void *d) { (void)fd; (void)d; }
static int paths(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size; (void)data; if (info->dlpi_name[0]) printf("LOADED %s\n",info->dlpi_name); return 0;
}
int main(int argc, char **argv) {
    const char *libraries[] = {"libwayland-client.so", "libwayland-server.so", "libxkbcommon.so",
        "libinput.so", "libudev.so", "libevdev.so", "libmtdev.so", "libseat.so", "libdrm.so"};
    for (unsigned i=0; i<sizeof(libraries)/sizeof(*libraries); ++i) {
        if (!dlopen(libraries[i], RTLD_NOW|RTLD_LOCAL)) { fprintf(stderr,"%s: %s\n", libraries[i],dlerror()); return 2; }
    }
    if (argc>1 && !dlopen("libgbm.so",RTLD_NOW|RTLD_LOCAL)) { fprintf(stderr,"GBM: %s\n",dlerror()); return 3; }
    struct wl_display *display=wl_display_create(); if (!display) return 4; wl_display_destroy(display);
    struct xkb_context *ctx=xkb_context_new(XKB_CONTEXT_NO_DEFAULT_INCLUDES); if (!ctx) return 5;
    if (!xkb_context_include_path_append(ctx, getenv("XKB_CONFIG_ROOT"))) return 6;
    struct xkb_rule_names names={.rules="evdev",.model="pc105",.layout="us"};
    struct xkb_keymap *keymap=xkb_keymap_new_from_names(ctx,&names,XKB_KEYMAP_COMPILE_NO_FLAGS); if (!keymap) return 7;
    struct xkb_state *state=xkb_state_new(keymap); if (!state) return 8;
    xkb_state_unref(state); xkb_keymap_unref(keymap); xkb_context_unref(ctx);
    struct udev *udev=udev_new(); if (!udev) return 9; udev_unref(udev);
    const struct libinput_interface interface={.open_restricted=open_restricted,.close_restricted=close_restricted};
    struct libinput *input=libinput_path_create_context(&interface,NULL); if (!input) return 10; libinput_unref(input);
    dl_iterate_phdr(paths,NULL); puts("CUSTOM_DESKTOP_LIBRARIES_OK"); return 0;
}
'''.replace('#include <errno.h>','#include <errno.h>\n#include <stdlib.h>'))
binary = work / "probe"
env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "XKB_CONFIG_ROOT": str(root / "usr/share/X11/xkb")}
obj = work / "probe.o"
subprocess.run([str(wrapper), "-c", str(source), "-o", str(obj)], env=env, check=True)
command = [str(wrapper), "-static-libgcc", "-Wl,--verbose", str(obj),
           "-lwayland-server", "-lxkbcommon", "-ludev", "-linput", "-ldl", "-o", str(binary)]
compiled = subprocess.run(command, env=env, text=True, capture_output=True, check=True)
(work / "link.log").write_text(compiled.stdout+compiled.stderr)
for opened in re.findall(r"attempt to open (.+) succeeded", compiled.stdout):
    path = Path(opened)
    if path.is_absolute() and not any(path.resolve().is_relative_to(p.resolve()) for p in [root, tools, work]):
        raise RuntimeError(f"Probe linked outside declared target inputs: {opened}")
loader = root / "usr/lib/ld-linux-x86-64.so.2"
arguments = [str(loader), "--library-path", str(root / "usr/lib"), str(binary)]
if "--gbm" in sys.argv:
    arguments.append("gbm")
result = subprocess.run(arguments, env=env, text=True, capture_output=True, check=True)
(work / "probe.log").write_text(result.stdout+result.stderr)
loaded = []
for line in result.stdout.splitlines():
    if line.startswith("LOADED "):
        path = line.removeprefix("LOADED ")
        if path == "linux-vdso.so.1":
            continue
        file = Path(path).resolve()
        if not file.is_relative_to(root):
            raise RuntimeError(f"Probe loaded outside target sysroot: {file}")
        loaded.append({"path": str(file.relative_to(root)), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
if "CUSTOM_DESKTOP_LIBRARIES_OK" not in result.stdout:
    raise RuntimeError("Missing target API success marker")
report = {"success": True, "kind": "target-loader-desktop-library-api-probe", "sysroot": str(root),
    "command": arguments, "packages": records, "loaded": loaded,
    "qualifier_sha256": qualifier_sha256, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "compiler_sha256": hashlib.sha256(compiler.read_bytes()).hexdigest(),
    "scope": "native target library loading; Wayland server lifecycle; distro XKB keymap compilation; udev/input contexts; no hardware/session qualification"}
(work / "report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({"success":True,"report":str(work / "report.json"),"stdout":result.stdout},indent=2))
