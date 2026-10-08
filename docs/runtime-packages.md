# Telorgon runtime packages

`packages/dbus/` and `packages/pipewire/` are ordinary source recipes. Their
runtime dependency declarations let the Python planner include the full target
closure in an image or dependency sysroot. No host D-Bus or PipeWire libraries
are copied into the target.

The D-Bus 1.16.2 source SHA-256 was checked against the upstream release's
[SHA-256 file](https://dbus.freedesktop.org/releases/dbus/dbus-1.16.2.tar.xz.sha256sum).
PipeWire 1.6.9 uses the upstream GitLab release-tag source archive, downloaded
over HTTPS and pinned by SHA-256 in its recipe. These source pins detect changed
downloads; package signatures and release authenticity policy are separate
release requirements.

The shared `runtime_native.py` adapter uses a source-built target compiler,
private package dependency sysroot, and explicit cross pkg-config paths. ELF
audits reject RPATH/RUNPATH and unresolved shared library requirements. Before
executing target programs on the build machine, the target loader's `--list`
output must resolve every library inside the supplied source-built roots.

PipeWire upstream sets an optional module RUNPATH to `/usr/lib/pipewire-0.3`.
This build removes those declarations from the extracted source using an exact
file digest and replacement count. A changed upstream file fails the build until
the overlay is reviewed. The before/after digests are recorded in the work
directory's `source-overlays.json`. All dependencies used by this profile live
in the normal target `/usr/lib` search directory.

Build qualification exercises actual daemons and clients:

- D-Bus starts a private Unix socket using EXTERNAL authentication. The target
  `dbus-send` calls `org.freedesktop.DBus.ListNames` and verifies the daemon's
  reply. Success is recorded as `CUSTOM_DBUS_IPC_OK`.
- D-Bus revision 2 enables its upstream systemd support against the independent
  source-built `libsystemd` package. A private inherited Unix listener is passed
  through the real `LISTEN_PID`/`LISTEN_FDS` contract to
  `dbus-daemon --address=systemd:`. The target libsystemd socket APIs adopt it,
  and an EXTERNAL-authenticated target client receives the actual daemon's
  name list. Success is recorded as `CUSTOM_DBUS_SOCKET_ACTIVATION_OK`.
- PipeWire loads its staged modules and SPA plugins, starts a private server,
  and answers target `pw-cli info 0` with a real `PipeWire:Interface:Core` object.
  The test uses private D-Bus addresses and disables RTKit, so it cannot reach
  host bus services. Success is recorded as `CUSTOM_PIPEWIRE_IPC_OK`.

These probes run on the host kernel through the target libc loader. They do not
replace booting the assembled guest and qualifying the Telorgon session there.
The socket inheritance fixture uses host Python solely to supply the private
listener and descriptor environment. It does not implement or impersonate a
systemd manager. PID 1 registering `org.freedesktop.systemd1` and logind session
creation still require the genuine guest's `dbus.socket`/`dbus.service` units
and their own guest qualification. The previous D-Bus revision was compiled
without this feature and rejects `systemd:` addresses; changing unit syntax
alone cannot enable socket inheritance.
The revision 2 [socket-activation receipt](../out/work/dbus/98e00235a9489576a26740e75f9b17756089ceef7ff531b4b298d6ea49395dc4/dbus-socket-activation-probe.json)
records this successful authenticated inherited-listener check. Its package
SHA-256 is `70f62a0eb601b1b7070bd0437c73e646832e7d3ed79304750d24fdfc9a122ceb`.
The actual daemon links the source-built `libsystemd.so.0`. The upstream
[D-Bus daemon manual](https://dbus.freedesktop.org/doc/dbus-daemon.1.html)
documents its address override and systemd activation options.
Some restricted build environments block Unix socket binding; the probe needs
a disposable worker or permission to bind its own private socket. It never
starts a host service, connects to host audio, or requires a global install.

The current PipeWire profile supplies the client API, server, native protocol,
SPA support, software audio/video conversion and test sources. Physical ALSA,
Bluetooth, JACK and camera backends are disabled. WirePlumber is now a separate
source-built session-policy package. The upstream PulseAudio protocol module is built but has not been
qualified. Since the disabled ALSA and JACK components are the upstream
LGPL/GPL exceptions, the selected compiled payload uses the MIT license.

Guest assembly must provide a private `XDG_RUNTIME_DIR` and start PipeWire in
the desktop session. `dbus-run-session` can establish a session bus around the
Telorgon launcher. A system bus additionally requires the `messagebus` user
and group, a machine ID, and `/run/dbus` before `dbus-daemon --system` starts.
The package does not create accounts or start services during image assembly.

With the bootstrap and verified source cache ready:

```sh
python3 build.py fetch dbus pipewire
python3 build.py build dbus pipewire --jobs 8
```

The ordinary planner builds dependencies first. Per-package logs, ELF audit
reports, loader resolution lists and runtime probe receipts live under `out/`;
the corresponding package state records the exact artifact digest and paths.
