# Testing and qualification

Use [environment setup](setup.md) and [build and run](build.md) first.
Keep all outputs under `out/`. A check establishes only the behavior and
artifacts in its receipt: compilation, host-kernel probes, guest installation,
boot and actual desktop interaction are separate checks.

## Local validation

```sh
python3 build.py validate
python3 -m unittest discover -s tests -v
```

During iteration, select the relevant test module with unittest's `-p` option.
Run the affected required checks before handoff. Native integration tests need
their recorded artifacts and an executor that permits private sockets; report
skips and environment failures explicitly. Do not replace a real ALPM or guest
check with a mock and call it runtime qualification.

## Source changes in a development VM

Create a separate named VM for automated or disruptive checks:

```sh
./run --profile desktop-dev --name gui-test --jobs 4
```

Edit the actual source, then keep QEMU running and deploy from another terminal:

```sh
python3 build.py deploy telorgon-shell telorgon-file-explorer \
  telorgon-settings-app telorgon-portal-picker --name gui-test --build-profile release
```

Test launcher hover, window controls, app sidebars, opening/closing apps and the
specific behavior changed. Inspect the screen for stale pixels after overlays
close, window movement and resize. Verify that restarted/reopened processes use
the updated packages. Preserve `out/vms/gui-test/deployment.json`, screenshots
and observations with the source identity and VM/base image.

Use a new image and unused VM name for runtime libraries, kernel, loader or
system policy. A saved disk keeps its installed state across image rebuilds.
The private deploy command updates application packages only.

## Hover performance

On WSL, verify the [WSLg frame-rate setting](setup.md#wslg-frame-rate), Windows
monitor mode and guest mode first. Record whether QEMU used KVM or TCG and the
guest resolution. Use release builds for renderer comparisons.

`tools/check-hover.py` drives real QEMU pointer input and collects guest shell
frame statistics. It requires a running named `desktop-dev` VM and an executor
that can connect to its private sockets. First enable profiling:

```sh
python3 tools/check-hover.py prepare --name gui-test
```

This restarts the desktop and closes apps. Open the launcher or app under test
and choose two points that enter/leave the relevant controls. For example, after
confirming the coordinates in a 1280×720 launcher:

```sh
python3 tools/check-hover.py measure --name gui-test \
  --point 78,376 --point 158,376 --seconds 8 \
  --output out/verification/hover/launcher
```

Coordinates depend on the display and window layout; inspect the screenshot to
establish that the test reached the intended controls. Each measurement retains
before/after screenshots, log intervals, installed versions, running shell hash,
PID stability, KVM state and the deployment receipt when present.

Compare identical input sequences and resolutions. Reporting intervals can
include preceding idle time or window-opening work; retain raw intervals and
state which complete intervals support a steady-hover comparison. CPU render
time is not end-to-end input latency, guest presentation FPS is not Windows
scanout FPS, and a headless renderer benchmark is not a VM interaction test.

Remove temporary profiling settings after testing:

```sh
python3 tools/check-hover.py cleanup --name gui-test
```

Cleanup also restarts the desktop. It preserves the installed packages and disk.

## Base-system boot

```sh
./run --profile console --headless --output out/verification/console
./run --profile systemd --headless --output out/verification/systemd
```

The console gate requires persistent-root identity and packaged manifest checks.
The systemd gate additionally checks PID 1, journald, udev, D-Bus and the
normal-user PAM/logind session. Reports bind the exact image digest, command,
serial log and screenshot. A missing assertion or timeout fails the command.
Neither gate establishes desktop behavior or physical hardware support.

## Desktop qualification

After preparing the runtime, SDK and normal application artifacts through the
desktop build, close that VM and build the independent probe:

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

Add `--window` to the VM command to view the bounded test. Require `success: true`
in the VM receipt and `passed: true` in `windows.json`. This profile is disposable
and separate from a saved development disk.

The gate launches the actual File Explorer and Settings windows under the
normal user's managed session. Their protocol logs must show configure/ack,
SHM buffer attach/commit, output entry and consumed-buffer release for the
matching surfaces. A buffer release alone does not establish presentation.
The independent Wayland probe requires focus, presentation feedback, a real
QEMU-injected Q key and a presented redraw before reporting success. The parser
binds the complete evidence to the VM's exact image digest and rehashes it.

Portal activation and service readiness do not establish chooser completion,
screen-sharing consent or media capture. Test those actual requests separately
when changing them; inspect the package's recorded capture capabilities first.

## Package upgrades and signatures

With the console runtime artifacts ready, compose and boot each private fixture:

```sh
python3 build.py image --test-upgrade
python3 build.py vm --image out/images/custom-distro-update-test.img \
  --expect CUSTOM_PACKAGE_UPGRADE_OK --output out/verification/upgrade
python3 build.py image --test-signed-upgrade
python3 build.py vm --image out/images/custom-distro-signed-update-test.img \
  --expect CUSTOM_SIGNED_PACKAGE_UPGRADE_OK --output out/verification/signed-upgrade
```

The ordinary fixture runs real guest `pacman -Syu`, checks versions/ownership
and preserves an edited configuration alongside `.pacnew`. The strict fixture
also requires signed package/database acceptance and rejects unsigned, tampered
packages and missing database signatures. Temporary signer secrets stay on the
fixture worker; the guest receives public verification material only.

These checks do not establish public repository delivery, key rotation or a
whole-system upgrade/reboot from a previous supported release.

## Password authentication

With systemd/PAM artifacts ready:

```sh
python3 tools/build-pam-auth-probe.py --make-fixture
python3 build.py image --profile systemd --test-pam-auth
python3 build.py vm --image out/images/custom-distro-pam-auth-test.img \
  --expect CUSTOM_PAM_AUTHENTICATION_OK --timeout 300 \
  --output out/verification/pam-auth
```

The private guest-only fixture tests the locked account, an incorrect password
and genuine successful password/account checks as UID 1000 through PAM and the
privileged shadow helper. It emits the completed marker only after restoring
and rehashing the original locked shadow file. Credentials use private files
and stdin, never command arguments or logs. Fixture packages/images are private
and must not be published or substituted for normal images.

PAM password checks and PAM/logind session creation are separate gates.
Interactive login and installer/account enrollment need their own tests.

## Target API and private IPC probes

After building the canonical package closure and native assembly toolkit:

```sh
python3 tools/qualify-desktop.py --gbm
python3 tools/qualify-desktop-services.py
python3 tools/qualify-desktop-settings.py
python3 tools/qualify-network-crypto.py tls signatures wpa
```

These compose private sysroots, audit linker/loader inputs and execute actual
target libraries/tools through the distro's loader on the host kernel. They
exercise desktop APIs, private D-Bus/dconf settings, TLS acceptance/rejection,
signature verification and WPA D-Bus IPC. They do not contact host session
services or establish guest scanout, physical Wi-Fi or audio/camera success.

## Retain evidence

Save the exact source origin/identity, package versions/hashes, base image and
deployment receipt, acceleration, display mode, input sequence and observations.
Keep screenshots and raw measurements alongside the results. Disclose what was
not tested and every skipped or blocked gate. Release qualification must use
the actual candidate artifacts; see [release requirements](release.md).
