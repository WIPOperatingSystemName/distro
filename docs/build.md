# Build and test the OS

Run these commands from the `distro` checkout on x86_64 Linux, including Ubuntu
inside WSL2. For Windows GUI setup and QEMU troubleshooting, see the
[emulator guide](https://github.com/WIPOperatingSystemName/.github/blob/main/docs/emulator.md).

## Host setup

Use Python 3.11 or newer. A full desktop source build needs substantial disk
space and memory; the desktop integration worker requires 128 GiB free and
16 GiB RAM. Start with four compile jobs, or two on smaller hosts.

On Ubuntu 24.04, install the declared build seeds and emulator tools:

```sh
sudo apt update
sudo apt install -y build-essential bison bc m4 perl autoconf automake \
  libtool pkg-config meson ninja-build fakeroot zlib1g-dev xz-utils \
  e2fsprogs gnupg python3 python3-jinja2 cmake clang libclang-dev \
  curl ca-certificates git qemu-system-x86 qemu-system-gui ovmf
```

Other distributions need equivalent tools. Bootstrap supplies the pinned private
Meson used by target builds. Host tools are development seeds; host runtime
packages are not copied into the image.

Install [Rustup](https://rust-lang.github.io/rustup/installation/index.html) if
needed, then install the compiler pinned by the loader and application recipes:

```sh
rustup toolchain install nightly-2026-10-06 --profile minimal \
  --target x86_64-unknown-uefi --target x86_64-unknown-linux-gnu
```

Initialize the sources at the commits recorded by the distro checkout:

```sh
git submodule update --init --recursive
```

Finish any component branch work before restoring submodules. The build command
uses the sources already present and records their identity; it never pulls,
resets or advances those checkouts.

## Build and run

Build and open the systemd OS test image:

```sh
python3 build.py run --profile systemd
```

The command audits the host, validates recipes, fetches verified sources, builds
and checks the bootstrap toolchain, builds the native package toolkit and runtime
packages, compiles Telorgon EFI, composes `out/images/custom-distro-systemd.img`
and opens QEMU. Failures stop the sequence and report the stage/log.

The window stays open after its startup checks pass. Close QEMU or press Ctrl+C
to stop. This profile has locked accounts and no interactive login. For a bounded
check that exits after success or failure:

```sh
python3 build.py run --profile systemd --headless
python3 build.py run --profile console --headless
```

Console/systemd disks use temporary snapshots. Reports, serial logs and
screenshots go to `out/verification/run-<profile>/`. Use `--output` to retain
separate runs and `--timeout` to change the 600-second startup deadline. A missing
assertion or timeout returns a failing exit status.

Use `--jobs 2` to reduce parallelism. `--offline` requires cached source archives,
Cargo inputs and the pinned Rust toolchain. Existing bootstrap/package receipts
are checked before reuse. The loader still invokes Cargo.

## Desktop build

The desktop pipeline additionally composes the SDK, checks its build inputs,
compiles the applications and creates `out/images/custom-distro-desktop-use.img`:

```sh
python3 build.py run
```

The default profile is `desktop-use`. It requires a compatible framework/app
source set. The integrated framework currently lacks `desktop-settings-linux`,
which Shell and Settings request; desktop compilation stops at Cargo feature
resolution until compatible component revisions are reviewed together. Removing
a feature name alone does not supply its API. The systemd test command above
builds independently of the application stage.

After a successful desktop build, QEMU starts a local `custom` session. Open
File Explorer and Settings from its launcher; close QEMU or press Ctrl+C to stop.
The default VM name is derived from the built image digest, so a changed image
gets a separate disk. Files and settings persist when reopening the same image.
VM files live under `out/vms/test-<digest>/`.

Use `run --name my-test` to deliberately reuse a named desktop disk, including
after rebuilding its base. To reopen it without building:

```sh
python3 build.py vm --use --name my-test
```

For component development or an imported source bundle, pass
`run --source-root /path/to/workspace`; the workspace must contain the Telorgon
framework, bootloader and application directories. See [source modules](sources.md)
and [contributing](contributing.md) for source snapshot and review rules.

## Individual stages

For diagnosing a stage or preparing a desktop without opening QEMU:

```sh
python3 build.py doctor
python3 build.py validate
python3 build.py fetch --bootstrap
python3 build.py bootstrap --jobs 4
python3 build.py bootstrap --check
python3 build.py native-toolkit --fetch --jobs 4
python3 build.py build --jobs 4
python3 build.py loader --online
python3 tools/compose-desktop-sdk.py
CD_DESKTOP_SYSROOT=$(python3 -c 'import json; print(json.load(open("out/sdk/current.json"))["sysroot"])')
python3 build.py apps prepare
python3 build.py apps check --sysroot "$CD_DESKTOP_SYSROOT"
python3 build.py apps build --sysroot "$CD_DESKTOP_SYSROOT" --online --jobs 4
python3 build.py image --profile desktop-use
```

Stop if a stage fails. `apps check` must report `ready_to_build: true`; it checks
SDK inputs, while compilation checks Rust features/APIs. Omit `--online` once
Cargo inputs are cached. `image` only composes already-built artifacts.

## Desktop qualification

After the desktop build, create the separate qualification image and require
both its guest checks and the app window/input report:

```sh
python3 tools/build-desktop-session-probe.py
python3 build.py image --profile desktop
python3 build.py vm --image out/images/custom-distro-desktop.img \
  --expect CUSTOM_DESKTOP_SESSION_OK --desktop-input --timeout 600 \
  --output out/verification/desktop
python3 tools/check-wayland-window.py \
  --vm-report out/verification/desktop/result.json \
  --output out/verification/desktop/windows.json
```

Add `--window` to view the bounded test. Require `success: true` in `result.json`
and `passed: true` in `windows.json`. The normal desktop launcher does not inject
test input or produce qualification receipts. See [desktop qualification](desktop-session-qualification.md),
[authentication](pam-authentication-qualification.md) and
[release requirements](release.md) for the scope of additional gates.
