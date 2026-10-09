# Set up the development environment

Use an x86_64 Linux host, or Ubuntu 24.04 under WSL2 with WSLg on Windows.
Python 3.11 or newer is required. Allow at least 128 GiB of free disk and
16 GiB of Linux-visible RAM for the full desktop build, matching the desktop
CI worker requirement. Start with four compile jobs; use two if memory is tight.
Build outputs and compiler caches can grow beyond the initial free-space budget.

Run the builder and QEMU as your normal Linux user. Host packages and the Rust
compiler below are declared development seeds; the distro's runtime is built
from source. Setup instructions describe actions for you to perform; the build
pipeline does not install host packages or change Windows configuration.

## Windows and WSL2

In **Windows PowerShell as Administrator**, install WSL and Ubuntu if needed:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows if requested and complete Ubuntu's first-run user setup.
For an existing installation, run in **Windows PowerShell**:

```powershell
wsl --update
wsl --version
wsl --list --verbose
```

Ubuntu must show version `2`. If necessary, convert it with
`wsl --set-version Ubuntu-24.04 2`, using the name from the list. WSLg supplies
the GUI connection used by Linux QEMU; follow Microsoft's
[WSL installation](https://learn.microsoft.com/en-us/windows/wsl/install) and
[GUI application setup](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gui-apps)
for Windows and graphics-driver prerequisites.

Keep the checkout and `out/` in the **Linux filesystem**, for example
`~/wip-os/distro`. Run build commands in Ubuntu, rather than PowerShell or a
checkout under `/mnt/c`.

### WSL memory and virtualization

Check what Ubuntu can use with `free -h` and `df -h .`. If WSL's memory cap is
too low, adjust it in Windows WSL Settings or merge appropriate values into
`%USERPROFILE%\.wslconfig`. For a host with enough RAM, an example is:

```ini
[wsl2]
memory=16GB
swap=8GB
nestedVirtualization=true
guiApplications=true
```

Leave RAM available for Windows. Nested virtualization depends on the host and
WSL kernel; this setting alone does not establish KVM availability. Microsoft
documents these controls in [WSL configuration](https://learn.microsoft.com/en-us/windows/wsl/wsl-config).
Save work and stop running guest VMs before applying changes with
`wsl --shutdown` in PowerShell; it stops every WSL distribution. Reopen Ubuntu.

### WSLg frame rate

For high-refresh testing, configure WSLg's presentation rate before measuring
the VM. Microsoft's [frame-rate explanation](https://github.com/microsoft/wslg/wiki/Controlling-WSLg-frame-rate)
documents a default ceiling of 60 FPS. Match the setting to your Windows
display's active rate; the example below uses **144 Hz**.

For Store-installed WSL, edit **`%USERPROFILE%\.wslgconfig` on Windows**
(for example `C:\Users\your-name\.wslgconfig`). For inbox WSL, use
`C:\ProgramData\Microsoft\WSL\.wslgconfig`. Merge into the existing section,
preserving other entries:

```ini
[system-distro-env]
WESTON_RDP_MONITOR_REFRESH_RATE=144
```

This is `.wslgconfig`; the memory settings above belong in `.wslconfig`.
Microsoft documents the [file locations and restart requirement](https://github.com/microsoft/wslg/wiki/WSLg-Configuration-Options-for-Debugging).
After stopping the guest and saving work, run in **Windows PowerShell**:

```powershell
wsl --shutdown
```

Reopen Ubuntu and verify that WSLg read the setting:

```sh
rg 'rdp_monitor_refresh_rate' /mnt/wslg/weston.log
```

If `rg` is not installed, use `grep` for this check. For 144 Hz, the log should
report `rdp_monitor_refresh_rate: 144000` (millihertz). If it does not, check the
file location, section name and that WSL fully restarted.

Then reopen the saved development VM:

```sh
cd ~/wip-os/distro
python3 build.py vm --use --development --name dev
```

On the first build, follow the checkout and build steps below instead.
The WSLg setting needs no distro image rebuild. Use QEMU on the intended Windows
monitor and check the guest display mode too: its refresh rate is a separate
limit. Guest FPS and renderer CPU timings do not prove the rate visible on the
Windows monitor. Remove the entry and restart WSL to restore its default.

## Host setup

In an **Ubuntu/Linux terminal**, install the declared seeds and emulator tools.
On Ubuntu 24.04:

```sh
sudo apt update
sudo apt install -y build-essential bison bc m4 perl autoconf automake \
  libtool pkg-config meson ninja-build fakeroot zlib1g-dev xz-utils \
  e2fsprogs gnupg python3 python3-jinja2 cmake clang libclang-dev \
  curl ca-certificates git qemu-system-x86 qemu-system-gui ovmf
```

Other Linux hosts need equivalent tools. The bootstrap builds its pinned private
Meson and additional generators under `out/`.

Install [Rustup](https://rust-lang.github.io/rustup/installation/index.html)
if it is missing, then install the recipe-selected toolchain:

```sh
rustup toolchain install nightly-2026-10-06 --profile minimal \
  --target x86_64-unknown-uefi --target x86_64-unknown-linux-gnu
qemu-system-x86_64 --version
qemu-system-x86_64 -display help
```

The QEMU display list must include `gtk`. Under WSLg, `DISPLAY` or
`WAYLAND_DISPLAY` should already be set; preserve the environment WSLg supplies.
Host systemd is not required for the guest's systemd desktop session.

### KVM and firmware

The launcher probes KVM and otherwise uses slower TCG software emulation.
Check device access as your normal user:

```sh
ls -l /dev/kvm
test -r /dev/kvm && test -w /dev/kvm
```

If `/dev/kvm` exists and access is restricted to the `kvm` group, add your user
to that group with `sudo usermod -aG kvm "$USER"`, then start a new login session.
Do not run the whole builder as root. The launcher's `VM acceleration: KVM`
message and VM report establish whether its initialization probe succeeded.
On WSL, hardware/Windows/WSL support may still prevent nested KVM.

OVMF is detected from the installed firmware paths. For an explicit matching
Ubuntu firmware pair:

```sh
export OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
export OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
```

Use code and variables from the same build and size, without Secure Boot
enforcement. QEMU copies variables into its private VM directory.

## Checkout and first run

For a new workspace:

```sh
mkdir -p ~/wip-os
git clone --recurse-submodules https://github.com/WIPOperatingSystemName/distro.git ~/wip-os/distro
cd ~/wip-os/distro
python3 build.py doctor
python3 build.py validate
./run --profile desktop-dev --name dev --jobs 4
```

The first run downloads verified archives, builds the toolchain and runtime,
builds Telorgon, composes the development image and opens QEMU. It can take
substantially longer than later builds. No separate manual bootstrap sequence
is needed. See [build and run](build.md) for the daily source/deploy loop.

For an existing workspace, inspect `git status` and `git submodule status` first.
Initialize missing submodules with `git submodule update --init --recursive`
only when doing so will preserve component work. Builds include uncommitted
source edits; do not restore or advance a checkout just to make a build run.
See [source modules](sources.md) and [contributing](contributing.md) before
synchronizing branches or changing pins.
