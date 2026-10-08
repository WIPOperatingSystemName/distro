# File types and application associations

The distro builds these upstream components as individual packages:

| Package | Version | Role |
| --- | --- | --- |
| file | 5.48 | Real libmagic, magic database, and `file --mime-type` |
| libxml2 | 2.15.4 revision 2 | XML parsing and `xmllint` |
| shared-mime-info | 2.5.1 revision 3 | Freedesktop MIME data, translations, cache generator, ALPM hook |
| desktop-file-utils | 0.28 revision 3 | Desktop entry validation, application handler database, ALPM hook |

Sources are pinned by SHA-256 and fetched from upstream over verified HTTPS.
The libxml2 hash matches the published
[release checksum](https://download.gnome.org/sources/libxml2/2.15/libxml2-2.15.4.sha256sum).
The other sources are the official
[file release](https://www.astron.com/pub/file/),
[shared-mime-info tag](https://gitlab.freedesktop.org/xdg/shared-mime-info/-/tree/2.5.1),
and [desktop-file-utils release](https://www.freedesktop.org/software/desktop-file-utils/).
These pins do not claim independent release-signature verification.

All target tools use the source-built compiler and package dependency sysroot.
ELF checks reject embedded library search paths and missing target libraries;
runtime probes resolve every library through the explicit target loader with
its cache disabled. No host XML libraries or MIME/magic databases are used.
The file recipe compiles the same pinned version as a private host generator
for `magic.mgc`; its executable hash and declared generator seeds are recorded
in the build work directory, and that executable never enters the OS package.
Shared MIME data uses the packaged genuine GNU gettext XML merger and its
target `update-mime-database` creates the complete installed cache.

Build probes validate a real XML document, identify PDF content through libmagic,
guess `image/png` through GLib using the generated MIME cache, validate a desktop
entry and register its `text/plain` handler. These probes establish package
behavior through the host kernel; launching and dispatching Telorgon apps in
the graphical guest remains a separate integration check.

Future pacman transactions run genuine target tools through post-transaction
ALPM hooks. Adding or removing `/usr/share/mime/packages/*.xml` refreshes the
MIME cache; changing `/usr/share/applications/*.desktop` refreshes
`mimeinfo.cache`. Image assembly disables execution of target transaction hooks
on the host. The image initialization must generate the desktop database after
all initial application packages are installed; the shared MIME package already
contains its generated baseline cache.

libxml2's optional Python binding, ICU, readline, legacy compressed input and
HTTP compatibility backend are disabled. file includes source-built zlib, xz
and libseccomp; bzip2, zstd, lzip and lrzip decompression backends are disabled.
Those are regular optional package extensions, not substitutes for the genuine
file identification API.
