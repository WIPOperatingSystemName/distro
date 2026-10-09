# Desktop session qualification

A prior local gate passed in QEMU/KVM against image SHA-256
`8fd942e2489cfc1d9ba4761844ad6992e817e906e15e7ce0d40719007fa4488b`.
See the [VM receipt](../out/verification/desktop-complete/result.json),
[actual app protocol qualification](../out/verification/desktop-complete/windows.json)
and [QEMU framebuffer](../out/verification/desktop-complete/screen.png).
Both apps have configured SHM windows, while the independent C probe supplies
keyboard delivery and compositor presentation/redraw proof. The screenshot
shows the actual apps and the probe's second color pattern. Portal broker
activation and the running network/power/polkit services are also checked;
chooser completion, audio hardware and ScreenCast streaming are not qualified.
These receipts apply to that image; rerun the [desktop gate](build.md#desktop-qualification)
to qualify a new source combination.

The initial source-built desktop profile uses Telorgon's existing CPU renderer
with DRM/KMS dumb buffers. The recorded shell overlay changes `Renderer::Vulkan`
to `Renderer::Auto` and `Capture::desktop()` to `Capture::new()`. Upstream
ScreenCast requires Vulkan and otherwise aborts after the session-ready message.
The package retains all four applications and the full desktop service closure,
sets the broker's ScreenCast preference to `none`, and records this capability in
`/usr/share/custom-distro/capabilities/telorgon-shell.json`. FileChooser uses the
real File Explorer backend. ScreenCast streaming needs a future source-built
Vulkan driver profile and separate runtime qualification.

## Session startup

Use the normal user's PAM/logind session and systemd user manager. The runtime
directory must belong to that UID, have mode 0700, and contain the existing user
bus. Telorgon does not start an extra D-Bus daemon itself. Start the user bus,
PipeWire daemon and WirePlumber before starting `/usr/bin/telorgon-session`.
The PipeWire minimum package has no upstream systemd units; the distro supplies
the actual daemon unit. The upstream WirePlumber unit orders itself after
PipeWire and D-Bus and binds its lifetime to PipeWire.

The built libseat enables only its seatd backend. Use `LIBSEAT_BACKEND=seatd` and
the distro's system seatd service; the user needs access to its seat-group socket.
Enabling systemd/logind for service policy does not add a logind backend to this
libseat build. Kernel DRM and input devices still need to work inside the VM.

The shell exports the actual Wayland socket and desktop identity through both
systemd's user-service environment and D-Bus's activation environment. It chooses
the first free `wayland-0` through `wayland-32` in the validated runtime directory.
Its real marker is `telorgon-session: ready on /run/user/UID/wayland-N`. Read the
socket from this result rather than assuming a fixed name. The CPU path can log
`telorgon-kms: Vulkan startup unavailable; preparing new software buffers` and
`telorgon-dmabuf: unavailable; client buffers use SHM`.

Keep `graphical-session.target` active through a desktop-specific session unit
before activating the portal broker. Systemd's upstream target has
`RefuseManualStart=yes` and `StopWhenUnneeded=yes`; directly running
`systemctl --user start graphical-session.target` is refused. A session-specific
target should use `BindsTo=graphical-session.target` and retain the running shell.
The managed shell service should precede the graphical target and finish its
bounded readiness check before portal activation. Telorgon does not implement
`sd_notify`; use `Type=exec` with an actual readiness helper, or a separately
qualified session owner. The public listener is a normal AF_UNIX stream socket,
so `test -S` is valid. A socket file alone does not qualify frame presentation.
The upstream `xdg-desktop-portal.service` has `Requisite=graphical-session.target`.
The document portal and permission store require the user D-Bus service. The
FileChooser implementation is selected by the packaged
`telorgon-test-shell-portals.conf`; its D-Bus activation launches
`/usr/bin/telorgon-file-explorer --portal`. The backend's real ready log is
`Telorgon File Explorer portal ready on
org.freedesktop.impl.portal.desktop.telorgon.FileExplorer`. Its FileChooser
version property is 4. Introspection/property access qualifies activation;
opening and completing a chooser dialog requires a separate request test.

## Actual app windows

Launch File Explorer with `telorgon-file-explorer --show /home/custom`. Its
declared GUI identity is `org.telorgon.FileExplorer` and normal title is `Telorgon Files`.
It also supports positional paths, `--select`, `--properties`, `--pick`, `--save`,
`--folder`, `--multiple`, `--title`, `--name`, `--download`, and bounded JSON
requests through `--request-stdin`. `--portal` is a background D-Bus service;
its continued existence alone does not prove a mapped window.

Launch Settings with `telorgon-settings-app --page=network`. Supported pages
are display, sound, network, power, battery and personalization; display is the
default. Its declared GUI identity is `org.telorgon.settings` and title is `Telorgon Settings`.
The shell settings API is `org.telorgon.TestShell.Settings` at
`/org/telorgon/TestShell/Settings`.

File Explorer, Settings and the capture picker enable the SDK's default
bundled-fonts feature. The shell disables SDK default features and embeds four
Inter faces itself; the capture picker also embeds those Inter faces. Source and
Rust font notices remain in each app package. Their software shaping and rasterization do not
require borrowing host font libraries or a host font collection.

Set `WAYLAND_DEBUG=client` separately for each application and retain its log.
Libwayland 1.24 emits `wl_callback#ID.done(TIME)`; older C logs use `@`, and
the Rust backend can emit `wl_callback@ID.done, (TIME)`. The parser accepts all
three formats. The inspected SDK's native window creation currently sets the
title without mapping the declared GUI identity to Wayland `set_app_id`.
Its software presenter uses softbuffer attachment/damage/commit without calling
winit's `pre_present_notify`, so these apps do not request surface frame callbacks.
Their actual display-sync callbacks cannot qualify presentation. Run:

```sh
python3 tools/check-wayland-window.py files.log --app-id org.telorgon.FileExplorer \
  --title 'Telorgon Files' --evidence mapped-shm
python3 tools/check-wayland-window.py settings.log --app-id org.telorgon.settings \
  --title 'Telorgon Settings' --evidence mapped-shm
```

This explicit mode follows the source-inspected app title through its toplevel,
xdg surface and wl_surface. It matches a real configure/ack, a buffer created by
wl_shm_pool then attached/committed to that surface, output entry for that same
surface and a matching consumed-buffer release. A release means the compositor
no longer uses that buffer; it does not prove scanout presentation. Numeric buffer
ID reuse and unrelated surfaces cannot pass. The report records missing app IDs
and missing frame callbacks explicitly. Its default strict mode still requires
an observed app ID and completed callback requested by that actual surface.
Actual scanout, presentation feedback and keyboard delivery use the next test.

## Presented window and input probe

Build with `python3 tools/build-desktop-session-probe.py`. The helper is compiled
against the immutable source-built desktop SDK. The actual target
wayland-scanner, invoked through that SDK's own loader, generates xdg-shell and
presentation-time bindings from the packaged XML. The receipt records compiler,
protocol, scanner, binary and resolved-library hashes. ELF inspection rejects
RPATH and incorrect interpreter; a missing session is rejected. The build
receipt explicitly does not claim guest runtime qualification.

Install the recorded binary as
`/usr/libexec/custom-distro/desktop-session-probe` in the qualification image.
Run it as the desktop user with the actual XDG_RUNTIME_DIR and WAYLAND_DISPLAY:

```sh
/usr/libexec/custom-distro/desktop-session-probe --timeout 40 --hold 8
```

It creates a 480x320 xdg-toplevel with four colored quadrants. It requires a
configure/ack, frame callback, compositor `wp_presentation_feedback.presented`
event and keyboard focus before printing
`CUSTOM_DESKTOP_PROBE_AWAIT_INPUT key=16`. Inject Q through QEMU's real emulated
keyboard at that point. A focused `wl_keyboard.key` press for evdev key 16 causes
a different shared-memory color pattern. Success requires a second frame and
presentation feedback for this redraw. Only then does it print
`CUSTOM_DESKTOP_WINDOW_INPUT_OK configured=1 initial_presented=1 keyboard_focus=1 key_pressed=1 redraw_presented=1`.
Discarded presentation, disconnect, missing focus or timeout fails the test.

Capture the QEMU screenshot while the probe is held. The probe's second pattern
uses cyan, white, dark gray and magenta quadrants. Preserve screenshot pixels and
its digest as evidence of scanout. A standalone ordinary client cannot enumerate
other applications: Telorgon deliberately filters foreign-toplevel discovery and
capture globals to explicit privileged connections. This qualifier respects that
policy and uses each app's own protocol log.

The guest can emit complete `CUSTOM_WAYLAND_LOG_BEGIN name=files` / corresponding
END blocks, plus equivalent name=settings blocks, into its serial evidence.
After the VM completes, run:

```sh
python3 tools/check-wayland-window.py --vm-report out/vm/RUN/result.json \
  --output out/vm/RUN/desktop-qualification.json
```

This applies the explicit title/SHM/output/release checks to both source-inspected
apps. It requires successful VM and keyboard-injection receipts, the exact probe
success line, both complete app logs, and an actual rehash of the boot image
matching the VM's image SHA. The result binds the app object/evidence sequences
to the image, VM receipt, serial log and parser digests. Console-only receipts
cannot qualify the desktop.

`build.py vm --desktop-input` injects Q only after the exact ready marker and
waits for the actual input/presentation success, both completed app-log END
markers, and the user-session success as well as its requested marker. This
keeps asynchronous journal forwarding from truncating a valid app log when the
system-service checker finishes first. The host parser remains mandatory.

`TELORGON_LATENCY_TRACE=/private/new-file.jsonl` additionally records bounded
surface publication/presentation, input flush and primary-flip events. It refuses
to overwrite files and writes its in-memory trace only on normal exit. Take the
screenshot first, then send handled SIGTERM and wait for the compositor to exit
before collecting that trace. Killing QEMU alone cannot flush it.

The capture picker is launched by the shell for real portal requests. There is
no independent public CLI: its parent sets TELORGON_PORTAL_PICKER=1 and protocol
version 1, passes an absolute Wayland socket, and exchanges bounded hello,
snapshot, preview and consent JSON over private stdin/stdout. Direct execution
without that transport fails. The CPU profile makes no claim about this
Vulkan-dependent sharing flow.
