# Desktop service packages

The desktop catalog uses source-built GLib/GObject/GIO, PCRE2, Lua, Duktape,
GUdev, WirePlumber, polkit, UPower, power-profiles-daemon, and the standard
xdg-desktop-portal broker. The broker links JSON-GLib, FUSE3, GdkPixbuf, PipeWire,
and GStreamer discovery libraries. NetworkManager and its real WPA supplicant,
MIME database, file/libmagic, and desktop-file tools are separate package recipes.
Recipes retain complete service dependencies and source SHA256 pins. The source
inspection records are under `out/sources/desktop-services-*.json`.

The initial CPU session disables the shell's Vulkan-only ScreenCast endpoint and
records that capability in the application package; FileChooser uses the real
File Explorer backend. The full broker and picker packages remain installed.
[Desktop session qualification](desktop-session-qualification.md) describes the
actual session order, app-window evidence, presented input probe and image-bound
VM report.

`desktop_services.py` uses a private ALPM dependency sysroot. GLib code generation
uses the package's own Python SDK modules with the declared host Python seed.
Compiled GLib/gettext generators execute through the source-built target loader;
getText XML/desktop policy merges use genuine GNU gettext. CMake is a declared,
hash-recorded generator for PCRE2 and PNG. Generator payloads stay inside private
work directories and target SDK data. Target Python CLI frontends are excluded
until a source-built Python/GI package is available; the power-profile D-Bus daemon
and Telorgon Settings still provide their real D-Bus interface.

The portal's image and sound validators retain Bubblewrap sandboxing. Bubblewrap
0.11.2 is compiled without setuid support and relies on Linux user namespaces.
The PNG profile uses current libpng 1.6.59 and a built-in GdkPixbuf PNG loader.
The sound profile contains real GStreamer playback/typefinding and WAV parsers;
additional codecs require explicit source package additions. Portal geolocation
and Flatpak-specific interfaces are disabled in this initial native application
profile. The document portal uses real FUSE3 and the upstream constrained,
root-owned setuid fusermount3 helper. io-uring and custom-IO FUSE extensions are
disabled. Systemd supplies udev and hardware database processing in the full image.

Polkit uses the source-built Duktape rules engine, logind tracking and PAM
`system-auth`. Its packaged PAM file is `/etc/pam.d/polkit-1`. `polkitd` needs a
service account created by image policy. The upstream agent helper and `pkexec`
retain root-owned setuid modes inside the guest. No permissive authorization rules
are added. Telorgon's current sources do not implement a GUI polkit authentication
agent, so password challenge prompts need additional application work; this does
not justify weakening the system authorization policy.

Packaged service activation paths:

| Package | Unit and bus interface |
| --- | --- |
| WirePlumber | user `wireplumber.service`, alias `pipewire-session-manager.service` |
| polkit | system `polkit.service`, `org.freedesktop.PolicyKit1` |
| UPower | system `upower.service`, `org.freedesktop.UPower` |
| power-profiles-daemon | system service, `org.freedesktop.UPower.PowerProfiles` / `net.hadess.PowerProfiles` |
| xdg-desktop-portal | user broker, permission store and document portal units/bus interfaces |

The Python assembler installs ordinary ALPM packages and image policy activates
units in the guest. Normal desktop services, privileged authentication, device
access, portal consent, real hardware and upgrades require QEMU/guest qualification.
The build process does not start these services on the host.

`python3 tools/qualify-desktop-services.py` composes actual source-built packages,
compiles a C probe with the source-built compiler, audits all linker inputs and
loaded DSOs, and checks GLib regex/JSON, Lua evaluation, Duktape evaluation, GUdev
type registration, and actual WAV parser creation. GIO authenticates to a private
short-lived source-built D-Bus daemon and performs a real GetId request. Socket
binding requires an executor that permits private Unix sockets. Reports are under
`out/qualification/desktop-services/`; this API/IPC probe does not certify the
complete desktop or access host audio/network devices.
