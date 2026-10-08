#!/usr/bin/env python3
"""Build an explicit minimal EFI kernel with the project's cross compiler."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess

source = Path(os.environ["CD_SOURCE_DIR"])
work = Path(os.environ["CD_WORK_DIR"]) / "linux-build"
stage = Path(os.environ["CD_STAGE_DIR"])
tools = Path(os.environ["CD_TOOLS"])
target = os.environ["CD_TARGET"]
work.mkdir()
mode = os.environ.get("CD_BUILD_MODE")
if mode not in {"target", "seed"}:
    raise SystemExit("Kernel requires an explicit seed or target compiler mode")
prefix = str(tools / "bin" / target) + "-" if mode == "target" else ""
compiler = os.environ["CD_CC"]
native = tools / "native"
for header in ["libelf.h", "gelf.h"]:
    if not (native / "include" / header).is_file():
        raise SystemExit("Kernel objtool requires the elfutils-native bootstrap stage")
os.environ["PKG_CONFIG_PATH"] = str(native / "lib/pkgconfig")
base = ["make", "-C", str(source), f"O={work}", "ARCH=x86_64", f"CROSS_COMPILE={prefix}", f"CC={compiler}",
        f"HOSTCFLAGS=-I{native / 'include'}", f"HOSTLDFLAGS=-L{native / 'lib'} -Wl,-rpath,{native / 'lib'}",
        "KBUILD_BUILD_USER=custom-distro", "KBUILD_BUILD_HOST=source-builder",
        "KBUILD_BUILD_TIMESTAMP=Mon Sep 1 00:00:00 UTC 2025"]
subprocess.run(base + ["tinyconfig"], check=True)
config_tool = [str(source / "scripts/config"), "--file", str(work / ".config")]
enabled = [
    "64BIT", "X86_64", "EXPERT", "PRINTK", "BUG", "BINFMT_ELF", "BINFMT_SCRIPT",
    "BLK_DEV_INITRD", "RD_GZIP", "EFI", "EFI_STUB", "EFI_MIXED", "EFI_RUNTIME_WRAPPERS",
    "TTY", "VT", "VT_CONSOLE", "VT_HW_CONSOLE_BINDING", "SERIAL_8250", "SERIAL_8250_CONSOLE",
    "SERIAL_8250_PNP", "DEVTMPFS", "DEVTMPFS_MOUNT", "PROC_FS", "SYSFS", "SHMEM", "TMPFS",
    "TMPFS_XATTR", "TMPFS_POSIX_ACL", "EXT4_FS_POSIX_ACL", "AUTOFS_FS", "FUSE_FS",
    "SYSCTL", "MULTIUSER", "FUTEX", "EPOLL", "SIGNALFD", "TIMERFD", "EVENTFD",
    "POSIX_TIMERS", "ADVISE_SYSCALLS", "AIO", "FHANDLE", "FILE_LOCKING", "INOTIFY_USER",
    "PARTITION_ADVANCED", "EFI_PARTITION", "MSDOS_PARTITION", "ACPI",
    "UNIX", "NET", "NETDEVICES", "NET_CORE", "PACKET", "INET", "IPV6", "PCI", "PCI_MSI",
    "VIRTIO", "VIRTIO_MENU", "VIRTIO_PCI", "NET_SCHED", "NET_SCH_FQ_CODEL",
    "VIRTIO_BLK", "VIRTIO_NET", "VIRTIO_CONSOLE", "BLOCK", "EXT4_FS", "MSDOS_FS", "VFAT_FS",
    "NLS", "NLS_CODEPAGE_437", "NLS_ISO8859_1", "RTC_CLASS", "RTC_DRV_CMOS",
    "INPUT", "INPUT_KEYBOARD", "KEYBOARD_ATKBD", "SERIO", "SERIO_I8042", "SERIO_LIBPS2",
    "FB", "FB_EFI", "FRAMEBUFFER_CONSOLE", "DUMMY_CONSOLE", "SMP", "X86_LOCAL_APIC",
    "X86_IO_APIC", "SCSI", "BLK_DEV_SD", "ATA", "SATA_AHCI", "USB_SUPPORT", "USB",
    "USB_XHCI_HCD", "USB_EHCI_HCD", "USB_STORAGE", "HID", "USB_HID", "HID_GENERIC",
    "CGROUPS", "MEMCG", "CGROUP_SCHED", "FAIR_GROUP_SCHED", "CFS_BANDWIDTH", "CPUSETS",
    "CGROUP_PIDS", "CGROUP_CPUACCT", "CGROUP_DEVICE", "CGROUP_FREEZER", "BLK_CGROUP",
    "BPF", "BPF_SYSCALL", "BPF_JIT", "BPF_UNPRIV_DEFAULT_OFF", "CGROUP_BPF",
    "NAMESPACES", "SYSVIPC", "POSIX_MQUEUE", "UTS_NS", "IPC_NS",
    "PID_NS", "NET_NS", "USER_NS", "SECCOMP", "SECCOMP_FILTER", "SECURITY", "KEYS",
    "AUDIT", "AUDITSYSCALL", "DMI", "DMIID", "DMI_SYSFS", "BLK_DEV_BSG", "EFIVAR_FS",
    "UNIX98_PTYS", "INPUT_EVDEV", "INPUT_MOUSE", "MOUSE_PS2", "INPUT_MISC", "INPUT_UINPUT", "VIRTIO_INPUT",
    "DRM", "DRM_KMS_HELPER", "DRM_BOCHS", "DRM_VIRTIO_GPU", "DRM_FBDEV_EMULATION",
]
for option in enabled:
    subprocess.run(config_tool + ["--enable", option], check=True)
disabled = ["DEBUG_INFO", "DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT", "MODULES", "LOCALVERSION_AUTO",
            "RT_GROUP_SCHED", "FW_LOADER_USER_HELPER"]
for option in disabled:
    subprocess.run(config_tool + ["--disable", option], check=True)
subprocess.run(config_tool + ["--set-str", "LOCALVERSION", "-custom"], check=True)
subprocess.run(config_tool + ["--set-str", "UEVENT_HELPER_PATH", ""], check=True)
subprocess.run(base + ["olddefconfig"], check=True)
actual = (work / ".config").read_text()
required = enabled
missing = [key for key in required if f"CONFIG_{key}=y\n" not in actual]
if missing:
    raise SystemExit(f"Kernel configuration dropped required options: {missing}")
unexpected = [key for key in disabled if f"CONFIG_{key}=y\n" in actual or f"CONFIG_{key}=m\n" in actual]
if unexpected:
    raise SystemExit(f"Kernel configuration enabled explicitly disabled options: {unexpected}")
subprocess.run(base + [f"-j{os.environ['CD_JOBS']}", "bzImage"], check=True)
release = subprocess.check_output(base + ["-s", "kernelrelease"], text=True).splitlines()[-1]
boot = stage / "boot"
boot.mkdir(parents=True)
image = boot / f"vmlinuz-{release}"
shutil.copy2(work / "arch/x86/boot/bzImage", image)
with image.open("rb") as payload:
    if payload.read(2) != b"MZ":
        raise SystemExit("Kernel does not contain an EFI boot stub")
shutil.copy2(work / ".config", boot / f"config-{release}")
vmlinux = work / "vmlinux"
readelf = str(tools / "bin" / f"{target}-readelf") if mode == "target" else "readelf"
elf_header = subprocess.check_output([readelf, "-h", str(vmlinux)], text=True)
elf_program = subprocess.check_output([readelf, "-l", str(vmlinux)], text=True)
elf_dynamic = subprocess.check_output([readelf, "-d", str(vmlinux)], text=True)
if not re.search(r"Machine:\s+Advanced Micro Devices X86-64", elf_header):
    raise SystemExit("Kernel vmlinux is not an x86_64 ELF")
if "INTERP" in elf_program or "NEEDED" in elf_dynamic:
    raise SystemExit("Kernel vmlinux unexpectedly requires a userspace loader/library")
record = {"version": release, "path": image.name, "efi_stub": True,
          "compiler_mode": mode,
          "compiler": compiler,
          "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
          "config_sha256": hashlib.sha256((work / ".config").read_bytes()).hexdigest(),
          "vmlinux_sha256": hashlib.sha256(vmlinux.read_bytes()).hexdigest(),
          "elf": {"machine": "x86_64", "interpreter": None, "needed": []},
          "required_config": {key: "y" for key in required},
          "disabled_config": disabled,
          "initramfs_compression": "gzip", "console": "ttyS0,115200",
          "scope": "experimental QEMU/UEFI console, systemd sessions and desktop portals; physical hardware not qualified"}
(boot / "kernel-build.json").write_text(json.dumps(record, indent=2) + "\n")
licenses = stage / "usr/share/licenses/linux"
licenses.mkdir(parents=True)
shutil.copy2(source / "COPYING", licenses / "COPYING")
print(json.dumps(record))
