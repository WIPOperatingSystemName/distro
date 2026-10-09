# Custom Distro

An independent, source-built Linux distribution coordinated by Python. The
desktop is Telorgon; package ownership and updates use source-built
pacman/libalpm. Target packages come from verified upstream source archives and
recorded snapshots of the pinned Telorgon source submodules.

## Verified so far

The console image boots through **OVMF → Telorgon EFI → our Linux kernel → our
initramfs → our ext4 root** in QEMU. Its own glibc, BusyBox and pacman runtime
are installed through real ALPM transactions. A separate disposable VM test runs
`pacman -Syu`, verifies ownership and changed payloads, preserves an edited
configuration, and checks the new `.pacnew` file.

The strict signed-upgrade VM accepts signed packages and repository metadata,
and rejects unsigned packages, tampered packages and unsigned databases. The
download/crypto stack also passed positive and negative private HTTPS checks.
A source-built systemd image passed PID 1, journald, udev, D-Bus and an active
UID 1000 PAM/logind session check. Exact receipts live under `out/verification`.

A separate password fixture passed locked-account and wrong-password rejection,
correct-password authentication as UID 1000, and restoration of the locked
defaults. The full desktop image passed managed Telorgon startup, real File
Explorer and Settings windows, and an independent Wayland keyboard/presentation
and redraw check. Its actual QEMU framebuffer is retained with the reports.

This is an experimental development system. Public update channels, interactive
login/account enrollment, installer, recovery, hardware qualification and
security-supported releases still require separate gates. Screen sharing is
disabled in the CPU renderer profile; a GUI polkit authentication agent is also
pending. Catalog and tooling checks pass on GitHub; full remote build and VM
qualification remain unverified.
The console profile exposes an unattended root console. Systemd/desktop profiles
use locked accounts and an automatic private qualification session. Development
repositories are explicitly unsigned; the strict-signature fixture is separate.

The current local evidence is:

| Check | Receipt |
| --- | --- |
| Package ownership/config-preserving upgrade | [upgrade](out/verification/upgrade/result.json) |
| Strict signed upgrade and rejection checks | [signed upgrade](out/verification/signed-upgrade-final/result.json) |
| System services and normal-user session | [systemd](out/verification/systemd-regular-user/result.json) |
| Genuine PAM password authentication | [authentication](out/verification/pam-authentication-marker/result.json) |
| Desktop boot, keyboard and redraw | [desktop](out/verification/desktop-complete/result.json) |
| Both actual application windows | [window evidence](out/verification/desktop-complete/windows.json) |
| Actual QEMU framebuffer | [screenshot](out/verification/desktop-complete/screen.png) |

## Project layout

```text
bootstrap/              source locks, compiler/libc stages and isolation probes
packages/<name>/        package.toml + build.py + patches/license inputs
packages/<app>/         application.toml for recorded local Telorgon projects
sources/                pinned framework, bootloader and application submodules
profiles/               packages selected for an OS image
system/                 distro identity, early boot and runtime policy
src/distro_build/       Python planner, fetcher, executor, packaging and VM tools
tests/                  archive, dependency, ownership and transaction checks
tools/                  repeatable runtime qualification probes
.github/workflows/      pull-request and integration checks
docs/                   build, package, contribution and release documentation
out/                    ignored source cache, private build roots and artifacts
```

Each package gets a private dependency sysroot assembled by libalpm, a fresh
staging directory, a frozen copy of its recipe/build code, a build identity and
an artifact digest. Per-package locks serialize shared local work. Native build
tools stay separate from target software. The host compiler and pinned Rust
toolchain are declared bootstrap seeds; host runtime packages never become the
target root.

## Build and boot

Run from this directory. See [bootstrap requirements](docs/bootstrap.md) for
the host seed tools. Nothing here installs packages into the host OS.

Initialize the source submodules before building:

```sh
git submodule update --init --recursive
```

A new checkout can use:

```sh
git clone --recurse-submodules https://github.com/WIPOperatingSystemName/distro.git
```

The parent repository pins
the exact source commits; [source modules](docs/sources.md) covers their layout
and updates.

```sh
python3 build.py doctor
python3 build.py validate
python3 build.py fetch --bootstrap
python3 build.py bootstrap --jobs 12
python3 build.py native-toolkit --fetch --jobs 4
python3 build.py build linux pacman --jobs 12
python3 build.py loader
python3 build.py image
python3 build.py vm --output out/verification/console
```

`loader` reads `sources/telorgon-bootloader` and `sources/telorgon` and
builds its own snapshot. The default loader and app builds are offline and use
the pinned `nightly-2026-10-06` Rust seed. Missing cached Cargo inputs must be
fetched explicitly with `--online`. QEMU and a matching unsigned OVMF pair are
host test tools. Existing system tools are preferred; the existing local VM
runtime is also supported. The VM uses a private firmware-variable file and a
snapshot of an ordinary image file.

The image is `out/images/custom-distro.img`. VM `result.json` records its actual
digest and serial evidence; `success: true` requires the persistent-root marker.
Use `--window` to display QEMU during a bounded verification run.

```sh
python3 build.py image --test-upgrade
python3 build.py vm --image out/images/custom-distro-update-test.img \
  --expect CUSTOM_PACKAGE_UPGRADE_OK --output out/verification/upgrade
```

The upgrade fixture is included only in the disposable test image. This proves
the implemented local transaction, not public signing, network delivery,
whole-system rollback, reboot migration or power-loss recovery.

```sh
python3 build.py image --test-signed-upgrade
python3 build.py vm --image out/images/custom-distro-signed-update-test.img \
  --expect CUSTOM_SIGNED_PACKAGE_UPGRADE_OK --output out/verification/signed-upgrade
python3 build.py build systemd --jobs 12
python3 build.py image --profile systemd
python3 build.py vm --image out/images/custom-distro-systemd.img \
  --expect CUSTOM_SYSTEMD_RUNTIME_OK --output out/verification/systemd
python3 tools/build-pam-auth-probe.py --make-fixture
python3 build.py image --profile systemd --test-pam-auth
python3 build.py vm --image out/images/custom-distro-pam-auth-test.img \
  --expect CUSTOM_PAM_AUTHENTICATION_OK --output out/verification/pam-auth --timeout 150
```

Images are retained as `out/images/objects/<sha256>.img`. The short image names
refer to the current candidate; build and VM reports identify the exact bytes.
The PAM test disk and fixture files are private and excluded from CI uploads;
they are never part of an ordinary image.

## Telorgon desktop build

The current pinned framework and apps fail Cargo feature resolution: Shell and
Settings require `desktop-settings-linux` and `services::desktop_settings`,
which Telorgon `880abac` does not provide. The 2026-10-08 local WSL2 run built
all 72 runtime packages, passed 97 tests and booted the systemd image through
WSLg, but produced no new desktop image. See
[current source compatibility](docs/contributing.md#current-desktop-source-compatibility)
and the [organization emulator guide](https://github.com/WIPOperatingSystemName/.github/blob/main/docs/emulator.md)
for the systemd boot command and the prerequisites for resuming the desktop build.

To try the already-built desktop locally, run:

```sh
python3 build.py vm --use
```

The normal `desktop-use` image starts Telorgon automatically as the local
`custom` user. Open apps from the desktop launcher. It contains no qualification
services, test clients or automatic test windows. This launcher has no boot
assertions or timeout and keeps running until you close QEMU. A private writable
disk and firmware are retained under `out/vms/custom`, so files and settings
survive subsequent launches. QEMU user networking supplies a virtual wired
adapter without host port forwarding. Normal accounts remain password-locked;
this profile explicitly provides local VM autologin.

Use `--name another-name` for a separate VM. An existing VM keeps its saved disk
even when a new base image is built. To compose a fresh normal desktop base from
the existing source-built packages, run `python3 build.py image --profile desktop-use`.
The `desktop` profile and `--window`/`--interactive` flags remain qualification
tools; CI continues to use those separately.

The app recipes package the shell, File Explorer, Settings and portal picker.
Build the source-built runtime closure and immutable SDK before compiling them:

```sh
python3 build.py fetch
python3 build.py build --jobs 12
python3 tools/compose-desktop-sdk.py
CD_DESKTOP_SYSROOT=$(python3 -c 'import json; print(json.load(open("out/sdk/current.json"))["sysroot"])')
python3 build.py apps prepare
python3 build.py apps check --sysroot "$CD_DESKTOP_SYSROOT"
python3 build.py apps build --sysroot "$CD_DESKTOP_SYSROOT" --jobs 4
python3 tools/build-desktop-session-probe.py
python3 build.py image --profile desktop
python3 build.py vm --image out/images/custom-distro-desktop.img \
  --expect CUSTOM_DESKTOP_SESSION_OK --desktop-input --output out/verification/desktop
python3 tools/check-wayland-window.py --vm-report out/verification/desktop/result.json \
  --output out/verification/desktop/windows.json
```

Add `--online` to app compilation only when its pinned Cargo inputs need fetching.
The first desktop profile uses CPU DRM/KMS rendering. Screen sharing is disabled
because the upstream capture path requires Vulkan. See
[desktop qualification](docs/desktop-session-qualification.md) for service,
window, presentation and input checks and their limits.

## Work on packages and applications

```sh
python3 build.py plan pacman
python3 build.py affected glibc
python3 build.py apps plan
python3 build.py apps prepare
python3 build.py apps check
python3 -m unittest discover -s tests -v
```

[Contribution workflow](docs/contributing.md) explains adding software and AI
assistance. [Packages](docs/packages.md) documents the real ALPM format and
transactions. [Boot media](docs/boot.md) describes the Telorgon/QEMU path.
[Release gates](docs/release.md) track requirements beyond the development build.

Generated candidate packages live below `out/packages/<name>/<build-identity>`.
Different private candidates may have the same version while development is in
progress. Publishing changed bytes requires a new package revision and a fresh,
reviewed repository snapshot. No release channel is published by these commands.

The build follows the source bootstrap approach described by
[Linux From Scratch](https://www.linuxfromscratch.org/lfs/view/12.4-systemd/),
with our own recipes, composition, Telorgon integration and package policy.

Project coordination code is MIT licensed. Upstream components retain their
licenses; release source and relinking obligations must be met before distribution.
