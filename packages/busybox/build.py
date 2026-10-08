#!/usr/bin/env python3
"""Build the minimal console statically against this project's GNU C library."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess

source = Path(os.environ["CD_SOURCE_DIR"])
work = Path(os.environ["CD_WORK_DIR"]) / "busybox-build"
stage = Path(os.environ["CD_STAGE_DIR"])
root = Path(os.environ["CD_SYSROOT"])
tools = Path(os.environ["CD_TOOLS"])
target = os.environ["CD_TARGET"]
if os.environ.get("CD_BUILD_MODE") != "target" or not (root / ".bootstrap-validated.json").is_file():
    raise SystemExit("BusyBox requires the validated source-built glibc bootstrap")
work.mkdir()
prefix = str(tools / "bin" / target) + "-"
compiler = prefix + "gcc"
libc = Path(subprocess.check_output([compiler, f"--sysroot={root}", "-print-file-name=libc.a"], text=True).strip())
if libc.resolve() != (root / "usr/lib/libc.a").resolve():
    raise SystemExit(f"BusyBox compiler selected libc outside its declared sysroot: {libc}")
# CONFIG_SYSROOT controls both compilation and scripts/trylink's library search.
base = ["make", "-C", str(source), f"O={work}", "ARCH=x86", f"CROSS_COMPILE={prefix}",
        f"CONFIG_SYSROOT={root}"]
subprocess.run(base + ["allnoconfig"], check=True)
enabled = {
    "STATIC", "ASH", "SH_IS_ASH", "ASH_JOB_CONTROL", "ASH_BASH_COMPAT", "ASH_ALIAS",
    "ASH_ECHO", "ASH_PRINTF", "ASH_TEST", "ASH_GETOPTS", "ASH_CMDCMD",
    "ASH_OPTIMIZE_FOR_SIZE", "FEATURE_EDITING",
    "FEATURE_SH_MATH", "FEATURE_SH_MATH_64",
    "FEATURE_TAB_COMPLETION", "LONG_OPTS", "FEATURE_VERBOSE_USAGE", "FEATURE_DEVPTS",
    "LS", "CAT", "ENV", "PRINTF", "ECHO", "MOUNT", "UMOUNT", "MKDIR", "MKNOD",
    "CTTYHACK", "SETSID", "SLEEP", "DMESG", "UNAME", "SYNC", "REBOOT", "POWEROFF",
    "HALT", "PS", "KILL", "TOUCH", "RM", "CP", "MV", "LN", "HEAD", "TAIL", "GREP",
    "SED", "FIND", "CHMOD", "CHOWN", "PWD", "TEST", "TRUE", "FALSE", "HEXDUMP",
    "STAT", "FEATURE_STAT_FORMAT", "ID", "WHOAMI", "TTY", "DF", "DU", "READLINK", "CUT", "WC", "SORT", "DATE", "DD", "CHROOT",
    "HOSTNAME", "FEATURE_MOUNT_FLAGS", "FEATURE_MOUNT_FSTAB", "SHA256SUM", "DUMPKMAP",
    "FINDFS", "SWITCH_ROOT", "BLKID", "VOLUMEID", "FEATURE_VOLUMEID_EXT", "FEATURE_VOLUMEID_FAT",
}
config = work / ".config"
lines = config.read_text().splitlines()
settings = {key: "y" for key in enabled}
settings.update({"SH_IS_NONE": "n", "SH_IS_HUSH": "n", "FEATURE_EDITING_HISTORY": "255"})
seen = set()
result = []
for line in lines:
    match = re.match(r"(?:# )?CONFIG_([A-Z0-9_]+)(?:=| is not set)", line)
    if match and match.group(1) in settings:
        key = match.group(1)
        seen.add(key)
        value = settings[key]
        result.append(f"# CONFIG_{key} is not set" if value == "n" else f"CONFIG_{key}={value}")
    else:
        result.append(line)
missing_symbols = set(settings) - seen
if missing_symbols:
    raise SystemExit(f"BusyBox release lacks configured symbols: {sorted(missing_symbols)}")
config.write_text("\n".join(result) + "\n")
subprocess.run(base + ["oldconfig"], input="\n" * 1024, text=True, check=True)
resolved = config.read_text()
required = ["STATIC", "ASH", "SH_IS_ASH", "MOUNT", "FINDFS", "SWITCH_ROOT", "VOLUMEID", "FEATURE_VOLUMEID_EXT", "FEATURE_STAT_FORMAT", "ID"]
missing = [key for key in required if f"CONFIG_{key}=y\n" not in resolved]
if missing:
    raise SystemExit(f"BusyBox configuration dropped required options: {missing}")
subprocess.run(base + [f"-j{os.environ['CD_JOBS']}"], check=True)
subprocess.run(base + [f"CONFIG_PREFIX={stage}", "install"], check=True)
binary = stage / "bin/busybox"
readelf = str(tools / "bin" / f"{target}-readelf")
headers = subprocess.check_output([readelf, "-l", str(binary)], text=True)
dynamic = subprocess.check_output([readelf, "-d", str(binary)], text=True)
if "INTERP" in headers or "NEEDED" in dynamic:
    raise SystemExit("BusyBox is not self-contained: dynamic interpreter/library dependency detected")
probe = subprocess.check_output([str(binary), "echo", "CUSTOM_BUSYBOX_OK"], text=True)
if probe.strip() != "CUSTOM_BUSYBOX_OK":
    raise SystemExit("Static BusyBox execution probe failed")
arithmetic = subprocess.check_output([str(binary), "ash", "-c", "echo $((1+1))"], text=True)
if arithmetic.strip() != "2":
    raise SystemExit("Static BusyBox shell arithmetic probe failed")
format_probe = subprocess.check_output([str(binary), "stat", "-c", "%u:%g:%a", str(binary)], text=True).strip()
if format_probe != f"{binary.stat().st_uid}:{binary.stat().st_gid}:{binary.stat().st_mode & 0o777:o}":
    raise SystemExit("Static BusyBox formatted ownership probe failed")
licenses = stage / "usr/share/licenses/busybox"
licenses.mkdir(parents=True)
shutil.copy2(source / "LICENSE", licenses / "LICENSE")
config_dest = stage / "usr/share/custom-distro/configs"
config_dest.mkdir(parents=True)
shutil.copy2(config, config_dest / "busybox.config")
print(json.dumps({"result": "CUSTOM_BUSYBOX_OK", "static": True, "compiler": compiler,
                  "libc": "source-built glibc 2.42", "sysroot": str(root), "binary": str(binary)}))
