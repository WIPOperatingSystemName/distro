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
`test_launcher.py` exercises distro updates and Python restarts through both
entry points using private local Git upstreams. It checks new app/component
inputs, preservation of local work, conflict/divergence stops and offline/help
behavior without compiling packages or launching a VM.
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

For browser changes, open Firefox from the launcher and visit an HTTPS page.
Confirm that text, images and JavaScript render, keyboard input and scrolling
work, and resizing restores the page without leftover pixels. Open an HTTP or
HTTPS link through `xdg-open` and confirm it reaches Firefox. Test audio through
the existing PipeWire session, and check the content sandbox in `about:support`.
Use a fresh named VM for browser runtime dependencies.

For Firefox build-environment changes, run the `test_firefox_build_temp.py`
regression and rebuild with `python3 build.py build firefox`. The regression
must reproduce the hashed-work-directory socket failure and complete a real
multiprocessing Manager round trip using the recipe's temporary environment.
Verify that the actual Firefox IPDL export completes and the final package
passes its payload audits. This build check does not qualify browser GUI behavior.

For terminal changes, open Foot from the launcher and with **Ctrl+Alt+T** while
the desktop has focus. In the terminal, run `tty`, `stty size`,
`infocmp "$TERM"` and `tput colors`; confirm a PTY, valid terminal definitions
and 256 colors. Resize the window and check that `stty size` changes and text
remains readable. Check colored output and Unicode text with the packaged font,
and confirm the launcher shows one Foot entry.

## System shell isolation

Build the [default development system shell](build.md#independent-system-shell) in a new
named `desktop-dev` VM. Use a release build when deploying the shell:

```sh
python3 build.py deploy telorgon-shell --name system-shell-test --build-profile release --offline
```

Through the private guest agent, verify that `telorgon-shell.service` is active,
its renderer has the dedicated account's UID and a cgroup under `system.slice`,
and `/run/telorgon-shell` belongs to that account with mode `0700`. Confirm its
`wayland-system` socket exists and the login view actually renders. Select the
`custom` account with the mouse and keyboard; confirm the password field masks
input and supports Backspace and Enter. The automatic
`custom-distro-desktop.service` must be inactive, and no `custom` login session
or `user@1000.service` should be required before authentication.

Through the guest agent as root, call `Snapshot` on `org.telorgon.SystemShell1`
at `/org/telorgon/SystemShell` with the same interface name; it returns the view
and selected username. Call `ShowLogin` and confirm that selection clears while
the renderer PID stays the same. Calls from `custom` must be denied, including
when addressed through the shell's unique bus name.

Record the renderer PID, start and stop `user@1000.service`, and check that the same PID
remains active. Check that the service cannot read `/home/custom`. Preserve the
source snapshot, installed package versions/hashes, base image digest, screenshot
and observed results under `out/`. These checks cover system shell ownership and
lifetime and login view.

In that disposable VM, test a locked account first. Back up `/etc/shadow` with
root-only permissions, enroll a temporary password for `custom` inside the guest,
then exercise both an incorrect password and the correct password through the
actual login UI. Do not bake passwords into images or put them in process
arguments, logs or evidence. Restore the original shadow file in cleanup and
verify its digest matches the backup.

After successful authentication, confirm the desktop renders, `Snapshot` returns
`desktop` and `custom`, and logind reports a local Wayland user session on seat0.
Verify the `--user-session` process has UID/GID 1000, belongs to that session's
scope, and owns `org.telorgon.UserSession1` on the user's bus. The renderer PID,
UID, private socket and home isolation must remain unchanged. Sign out through
the desktop, check the user process and logind session disappear, and confirm
the same renderer returns to an unselected login view. Repeat login/logout, and
exercise termination of the user process and session manager while logged in.
Calls to `org.telorgon.Session1` from ordinary users must be rejected through
both its public and unique bus names. Record each observed result separately;
these checks establish authentication, session lifetime and isolation.

For application integration, deploy all affected applications after framework
changes using the command in the [build guide](build.md#independent-system-shell).
Sign in and launch File Explorer and Settings from the desktop panel. Interact
with the windows and capture the rendered results. Confirm the processes have
the authenticated user's UID, home, session bus and session scope, and use
`WAYLAND_DISPLAY=telorgon-<session-id>` in that user's runtime directory. The
socket must be owned by that user with mode `0600`; a connection from a different
UID must be disconnected even if it can reach the path. Test a desktop entry in
the user's own XDG applications directory to establish that discovery runs as
the user while the renderer remains unable to read the home directory.

Sign out while applications are open. Confirm their processes and session socket
disappear, their windows are removed, and the renderer PID stays unchanged.
Repeat login and application launch after terminating the user helper and after
killing the session manager. Record package hashes, source snapshots and each
actual observation under `out/`. These checks do not qualify wallpaper, audio,
portals or X11 applications in the system shell.

For display settings, open Settings through the panel and confirm the connected
monitor and available controls render. Increase interface scale and verify that
the window moves into the smaller workspace with its confirmation buttons
reachable. Save a preview and exercise both Keep and Revert. Leave a preview
unconfirmed and verify that the display rolls back after 20 seconds. An invalid
configuration or expired token must return an error while subsequent snapshots
and valid previews still work. Confirm the settings endpoint on the user's bus
belongs to the user helper, and direct calls from that user to the renderer's
system-bus display method are denied through both public and unique bus names.

Verify that Keep saves the preference under the user's home. Sign out during a
preview, confirm the login view returns to default display settings, and sign in
again to verify the confirmed preference is restored. Attempts to use a retired
session ID or preview token must fail. Repeat helper and broker crash recovery
with a pending preview and confirm the renderer PID remains unchanged. Preserve
the original guest preferences and locked-account credentials during cleanup;
record source/package identities, screenshots and actual results under `out/`.

For personalization, select two distinct managed photos through Settings and
verify each changes the actual desktop background. Switch back to the first
photo to check that it uploads fresh pixels. Confirm that saved preferences and
the background library belong to the user, and that the same background returns
after login. The login screen must always return to the default background after
logout or a helper/broker failure. Direct user calls to the renderer's background
method, including through its unique bus name, and calls with a retired session
ID must be rejected.

Try a missing image, a symlink, corrupt image bytes, and a managed filename whose
content digest does not match. Each must leave the displayed background intact
and permit a later valid selection. Test a missing saved background at login:
the default must render with an error available through the settings snapshot,
without rewriting the saved preference. Deleting the applied or saved background
must fail; deleting an unused managed photo must succeed. Verify pixel transfer
rejects mutable descriptors, non-files, invalid dimensions and inconsistent
lengths. Preserve the original user preferences/library and guest credentials in
cleanup. These checks do not establish the file chooser or audio integration.

For sound, verify no user audio servers start before authentication. Sign in and
check PipeWire, WirePlumber and Pulse compatibility run with the user's UID,
home and runtime directory. Servers started by the helper must belong to its
logind session scope. Open Sound in Settings, select an output/input, change
volume and mute, save, and verify observed device state as well as the user-owned
preferences file. Sign out and sign in to verify restoration. A live external
volume adjustment must remain in effect rather than being overwritten by the
periodic restore worker.

Kill the private guest's audio server and verify its supervised restart, client
reconnection and restoration of saved preferences. WirePlumber's clean exit
on disconnection must also restart, with bounded retries. Missing devices and
invalid preferences must report errors and allow a later valid reload. Sign out
with audio active, then repeat helper and broker crash recovery: session-owned
audio processes must end while the renderer PID remains unchanged. An existing
audio stack reused from another login remains owned by that login. Keep host
playback/capture and physical device qualification separate from virtual device
state checks, and restore original guest preferences and credentials in cleanup.

For file dialogs, deploy the shell and Settings packages in a private system
shell VM. Confirm no portal services start before authentication. After login,
check that the desktop portal, document portal, permission store and FileChooser
backend have the user's UID, home, display and logind scope. Open Personalization
in Settings and use Add Photo to select an image from the user's home through
the actual Telorgon chooser. Verify its image filter, imported managed file,
user ownership and displayed wallpaper after Apply. Cancel a second request and
confirm the library and preferences stay unchanged. Test file-open and file-save
requests through the frontend separately; readiness alone is insufficient.

Close a frontend request while its dialog is open and confirm the child exits.
Direct calls to the backend from an ordinary application must fail. Kill the
backend and frontend separately, verify their supervised restart, and complete
a later request. Sign out with a chooser open, then repeat helper and broker
crash recovery. Portal processes, dialogs and requests must disappear while the
system renderer remains alive. Sign in again and complete another selection.
Record source/package identities, the VM image digest, screenshots and actual
responses under `out/`; restore guest credentials and preferences during cleanup.
These checks qualify local file dialogs, with screen sharing and sandboxed
document grants requiring their own runtime tests.

For the final combined check, compose `desktop-dev` without a shell override and
boot a new named VM. Verify that it selects the system compositor, exposes only
`custom`, keeps that account locked, and starts no personal bus or services
before authentication. Back up guest credentials and preferences, then exercise
`dev-password` with a temporary password supplied only through its hidden prompts
or an in-memory test harness. Confirm a wrong password is rejected and the
correct password opens the desktop in the same renderer process.

Launch Files and Settings from the panel. Import and save a wallpaper through
Add Photo, save display and sound settings, and verify actual display pixels and
audio state. Reboot the guest, sign in with the enrolled password, and check
restored preferences and application launch. Sign out with a dialog open and
repeat helper, broker and renderer crash recovery. The first two must retain the
renderer; a renderer crash must revoke the old user session and return to login
before another sign-in. Restore the original shadow digest and preferences,
power off cleanly and preserve exact image/package/source identities under
`out/`. This qualifies the single-user VM flow; screen sharing, X11 applications
and physical audio devices need separate qualification.

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
