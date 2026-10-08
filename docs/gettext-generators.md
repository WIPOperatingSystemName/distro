# GNU gettext generators

The `gettext` recipe builds GNU gettext 1.0 from its pinned upstream archive.
Its C tools use the distro's own loader and C library. The included upstream
libxml and libunistring sources provide XML/Unicode processing, with no host
parser library or substitute translation tool. Java, C#, D, Go, Modula-2 and
the C++ runtime binding are disabled in this initial package slice.
The optional Python `spit` command is omitted until a target Python SDK is
packaged. Shell scripts require the source-built BusyBox runtime; GNU
`gettextize`/`autopoint` development workflows also require an Autoconf/Automake
SDK, which is separate from these qualified Meson translation generators.

The package preserves `msgfmt`, `msgmerge`, `xgettext`, the other enabled GNU
tools and upstream SDK data. Build probes require a genuine French translation
to appear in an XML component and a desktop template, in addition to own-loader
version and ELF dependency/path checks.

Cross builds can use `distro_build.gettext_native.prepare_gettext_tools(sysroot,
work)`. It returns a private directory of wrappers for the three generator
commands. Each wrapper invokes the installed target tool through the declared
target loader and library directory. GNU gettext still performs the actual
translation merge. This works for the current x86_64 host/guest architecture;
another target architecture needs a real execution wrapper such as QEMU user
emulation or a native generator built from the same pinned source.

The wrapper sets `GETTEXTDATADIR`, `GETTEXTDATADIRS` and `XDG_DATA_DIRS` to the
private dependency sysroot's data. A consumer may add its own ITS rules through
`GETTEXTDATADIRS`, within that sysroot or its work/source directory. Host rule
directories are rejected. Loader preload/audit/search environment overrides are
removed, and the helper records the tool, loader and wrapper byte digests in
`gettext-tools/receipt.json`. Consumer package dependency archives and frozen
helper source remain part of the build identity.
