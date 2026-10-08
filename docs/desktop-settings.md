# Desktop settings runtime

Telorgon's managed desktop session invokes `dconf compile` before opening its
Wayland socket. It compiles a private file database under the user's validated
runtime directory, containing the GNOME window-decoration `button-layout`
preference. It preserves the inherited dconf profile and adds a `file-db:` entry;
existing user preferences retain priority. The session deletes its private
configuration when it exits. This behavior requires the real dconf compiler.

The source-built `dconf` package retains its CLI, client library, GIO settings
backend, D-Bus activation file and upstream systemd user unit. The complete
`gsettings-desktop-schemas` package includes the corresponding
`org.gnome.desktop.wm.preferences` schema, upstream enum definitions and compiled
schema cache. Telorgon's Rust clients draw with their own toolkit; these schemas
also make its decoration defaults available to applications using GSettings.

Both packages use the official GNOME 51.0 releases. Published checksum files
verify [dconf's source archive](https://download.gnome.org/sources/dconf/51/dconf-51.0.sha256sum)
and [the desktop-schema archive](https://download.gnome.org/sources/gsettings-desktop-schemas/51/gsettings-desktop-schemas-51.0.sha256sum).
The verified dconf archive includes GVDB source; its private Meson build permits
only that bundled subproject and disables network downloads. The target compiler
and dependency libraries come from this project's validated bootstrap and ALPM
artifacts. Packaged GLib generators and GNU gettext handle schema enums and
translations. Optional Vala bindings, documentation and introspection are disabled.

The build invokes the actual target `gio-querymodules` and
`glib-compile-schemas` through the project's loader to generate the initial
caches. ALPM post-transaction hooks refresh the GIO module and schema caches when
packages are installed, updated or removed. Initial image assembly can suppress
hooks because these two packages already carry their generated caches; adding
more modules or schemas requires regenerating the combined cache.

`python3 tools/qualify-desktop-settings.py` installs the actual canonical package
closure in a private ALPM sysroot. It compiles the same private defaults that
Telorgon uses, runs a source-built D-Bus daemon and dconf writer on a private Unix
socket, then checks the value using a target GSettings client. The client must
load the actual packaged dconf plugin. A user override must persist to the private
user database and be readable in a new process. The report hashes the packages,
loaded shared objects, databases, compiler and qualification source. This test
does not contact the host session bus or change the host's preferences.

Shell package revision 5 adds both runtime dependencies while preserving the
already verified CPU-profile Rust compilation, source snapshot and SDK receipt.
Guest compositor and mapped-window qualification remains a separate QEMU check.
