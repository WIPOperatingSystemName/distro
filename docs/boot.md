# Custom Distro boot media

For ordinary local use, `build.py vm --use` launches the `desktop-use` image
with a writable private disk and OVMF variables retained under `out/vms/custom`.
It runs until the user closes QEMU, permits guest reboot, supplies a USB tablet
and keyboard, and attaches a virtio network adapter to QEMU user networking.
It does not inspect guest success markers or inject test input. The normal
image omits qualification units/probe and opens a genuine automatic UID 1000
PAM/logind desktop. Existing saved disks are reused rather than reset when the
base image changes. `--name` creates a separate named VM.

The default UEFI application is built from Telorgon source. It launches Custom
Distro's source-built Linux EFI-stub kernel and source-built early-userspace
initramfs. The console profile has a BusyBox console. Without a root argument it
runs in RAM; with `root=UUID=...` it locates the image's ext4 root and switches into
the runtime selected by `/etc/custom-distro/init-command`. That policy initially
names `/usr/lib/custom-distro/init` for the console profile. The systemd and
desktop profiles select `/sbin/init` through their package-owned policy without
changing the early-userspace API. Console success is `CUSTOM_DISTRO_BOOT_OK`;
systemd services, desktop windows/input and package upgrades have separate
qualification receipts. Boot success alone does not establish installation,
recovery or hardware compatibility.

`boot.build_loader` snapshots the pinned Telorgon framework and bootloader source
trees into the distro output directory. It excludes Git metadata, build output
and vendor caches, records every input file's hash and executable mode, records
Git HEAD/dirty state where available, and generates the distro's embedded
configuration. A fresh Cargo build uses `nightly-2026-10-06`, a locked dependency
graph and a private target directory. Offline builds require that toolchain and
the Cargo source cache to exist on the host. No source submodule checkout is
modified, and no prebuilt EFI artifact is substituted for compilation.

`boot.build_initramfs` packs the target root as a deterministic `newc` archive
with root ownership and gzip timestamp zero. The source-built BusyBox provides
the executable and applets referenced by `system/init`. Device files are
provided by the guest's devtmpfs, not by privileged host `mknod` operations.

`boot.stage_esp` copies the fresh Telorgon application to the UEFI removable-media
path `EFI/BOOT/BOOTX64.EFI`, keeps its normal path under `EFI/Telorgon`, and writes
`EFI/Telorgon/boot.toml`. Linux and its initramfs live under `EFI/Custom`. That
configuration contains only this distro's boot payload. It does not download or
boot another distribution's kernel, initramfs, root filesystem, or ISO.

`media.build_disk` creates a protective MBR, matching primary and backup GPT
tables, and a FAT32 EFI partition directly in an ordinary image file. An optional
ext4 root is populated by `mkfs.ext4 -d` against another private image file.
The root files are recorded as root-owned using one
`fakeroot` process for metadata normalization and filesystem creation. Real
host ownership remains unchanged. Systemd and desktop images additionally
record the home directory's UID/GID 1000 ownership and create service/runtime
directories through the actual guest sysusers/tmpfiles policy.
Neither host path mounts filesystems, uses loop devices, partitions a physical
disk, or writes host firmware variables. The guest's early init mounts only its
virtual root. Persistent-root boot additionally emits
`CUSTOM_DISTRO_PERSISTENT_ROOT_OK`; the runtime prints this only after validating
its OS identity and packaged build manifest.

`vm.run` validates the disk tables, then starts QEMU with a private copy of the
OVMF variable template and a temporary disk snapshot. OVMF code and the input
image remain unchanged. Serial output, QEMU diagnostics, the exact command,
QMP events and a framebuffer screenshot are kept in the run directory. The
runner stops after the marker or a bounded timeout. The existing read-only
Telorgon QEMU runtime may serve as a host test tool; its Ubuntu libraries and
firmware are not files in the target OS or package inputs.

Completed disks are retained at `out/images/objects/<sha256>.img`. Convenient
names identify the latest candidate, while the VM resolves a managed candidate
to its measured content object before executing it. Reports retain the exact
disk digest. Password-test disks remain mode 0600 and are excluded from CI
uploads. Normal development disks contain locked accounts for systemd/desktop
profiles and an unattended root console for the console profile.

The current Telorgon automatic policy boots only a single-target configuration.
A selector containing normal and recovery entries requires explicit selection;
QMP can send Enter for that test. A source-built systemd EFI stub can later
combine the distro kernel, command line and initramfs into a UKI without changing
the loader's responsibility.

Before an installable release, boot generation needs versioned payloads,
safe failed-update retention, and recovery independent of the Telorgon binary
and configuration. A recovery menu item alone cannot repair a broken primary
loader. An independently bootable rescue image and a separate firmware recovery
entry must be qualified. Secure Boot, encrypted-root storage, automatic boot
counting/fallback and installation to physical disks are not implemented.
Source-built pacman has passed local and strict signed fixture upgrades;
public update delivery and whole-system upgrade/reboot recovery remain release
gates. The software-rendered QEMU desktop has separate window/input evidence.
Physical graphics, networking, audio and UEFI behavior still need qualification.
