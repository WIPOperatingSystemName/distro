# Packages and the native assembly toolkit

Custom Distro compiles its own target software. Its Python builder exports
standard ALPM binary packages, and source-built pacman/libalpm performs real
package transactions. Using this format does not import Arch packages or its
filesystem. The exporter is not a second package manager or dependency solver.

## Current capabilities

The backend produces deterministic `.pkg.tar.xz` archives with `.PKGINFO` v2,
`.BUILDINFO` v2, and gzip-compressed `.MTREE` v2. It retains version/revision,
runtime dependencies, provides/conflicts/replacements, package architecture,
configuration backup paths, numeric ownership, modes and symlinks. It does not
strip binaries or create implicit debug packages. A package stage determines
the complete payload; split outputs must be staged separately by the builder.

The standard-format references are the upstream [package specification](https://alpm.archlinux.page/specifications/alpm-package.7.html),
[PKGINFO](https://alpm.archlinux.page/specifications/PKGINFO.5.html),
[BUILDINFO](https://alpm.archlinux.page/specifications/BUILDINFO.5.html), and
[ALPM-MTREE](https://alpm.archlinux.page/specifications/ALPM-MTREE.5.html).

The development native toolkit has been built from pacman 7.1.0, libarchive
3.8.9, OpenSSL 3.5.9, and XZ 5.8.4. Exact source URLs and SHA-256 pins live in
`src/distro_build/packaging/sources.toml`. These are upstream source archives.
The host compiler, host libc and host zlib are declared seed inputs for these
native assembly tools. They are not final OS packages and are never copied into
the target image. Dependency/build logs and provenance live below
`out/native-toolkit/`.

This development toolkit deliberately disables curl and GPGME. It accepts only
explicit local bootstrap transactions; its local repository databases are
unsigned test artifacts. A public release requires a qualified source-built
download/signature stack, trusted keys, signed packages/repository databases,
and release/upgrade gates. This native assembly policy is separate from the
target pacman package, whose curl/GPGME stack and signed local guest transaction
have passed the checks in [Authenticated updates](authenticated-updates.md).
Those checks do not establish public-release readiness.

## Export API

```python
from pathlib import Path
from distro_build.packaging import export_package, inspect_package

artifact = export_package(
    Path("out/stages/example"),
    Path("out/packages"),
    {
        "name": "example",
        "version": "1.0.0",
        "revision": 1,
        "arch": "x86_64",
        "description": "Example application",
        "licenses": ["MIT"],
        "depends": ["glibc>=2.42"],
        "backup": ["etc/example.conf"],
    },
    source_date_epoch=1761955200,
    provenance={"sources": {"example": "a verified full commit or archive digest"}},
)
print(artifact.path, artifact.sha256)
print(inspect_package(artifact.path))
```

The builder must resolve and lock dependencies before compilation. Runtime
dependencies refer to installed binary outputs, not source-recipe directories.
Native build generators and target libraries must be distinguished in the build
graph. Architecture alone does not describe libc/ABI compatibility; use separate
repositories and cache identities for different target ABIs.

`export_package` also accepts `ownership={"path": (uid, gid)}` and an optional
`install_script=Path(...)`. Default payload ownership is `0:0`, independent of
the builder's account. Preserved configurations must name packaged regular
files with relative paths, such as `etc/example.conf`.

Source and payload hashes, flags, toolchain/sysroot, language locks, referenced
files/hooks, dependency artifacts, and builder/exporter identity belong in the
builder's input/provenance manifest. `.BUILDINFO` hashes an actual deterministic
packaging descriptor stored beside the archive as `.PKGBUILD`; the descriptor
does not pretend to reproduce compilation. The complete compilation provenance
is the adjacent `.build.json`, which release manifests must bind to the package.

Equal payloads and the same reproducibility timestamp produce equal archive
bytes. The exporter refuses to overwrite different bytes under the same
name/version/architecture filename. Increment the package revision for changed
published payloads or installation metadata, including required ABI rebuilds.

## Archive verification

`inspect_package` reads without extraction. It rejects traversal, duplicate
members, children below symlinks, reserved metadata collisions, oversized
archives/metadata, invalid metadata and mismatches between `.MTREE` and actual
archive contents. Final archives are checked after export, not just staged
files. Signature checks belong to native pacman and the release trust system.

This initial exporter supports regular files, directories and symlinks. It
represents hardlinked staging files as separate regular files. Device nodes,
FIFOs, sockets, imported hardlink archive entries and extended attributes are
rejected. Capabilities/ACLs/xattrs need a qualified extension before packages
requiring them can be shipped; they are never silently discarded. Runtime
devices should normally be created by the OS device manager.

## Build the local toolkit

Run from the project root after preparing the four verified source archives
under `out/native-toolkit/sources/` using their upstream filenames:

```sh
PYTHONPATH=src python3 -c 'from pathlib import Path; from distro_build.packaging.bootstrap_toolkit import build_development_toolkit; build_development_toolkit(Path("out/native-toolkit"), jobs=4)'
```

`fetch=True` explicitly enables HTTPS fetching through this API. Missing sources
otherwise fail before compilation. All installs use private prefixes; pacman
installation is additionally captured with `DESTDIR` so optional upstream
integration cannot write host paths. The resulting tools are under
`out/native-toolkit/prefix/bin/`, with private libraries and a `toolkit.json`
record. Network approvals, if required by the execution environment, are handled
outside the builder.

The generic `build_toolkit(source_dir, prefix, work_dir, env, development=False)`
also accepts a separately prepared dependency environment. Production mode
requires curl/GPGME rather than quietly weakening signature policy. It is a
build interface, not evidence that the production stack has been qualified.

## Real local package transactions

```python
from pathlib import Path
from distro_build.packaging import PacmanToolkit

toolkit = PacmanToolkit(Path("out/native-toolkit/prefix"))
root = Path("out/roots/package-test")
toolkit.install(root, [artifact.path], bootstrap=True,
                expected_hashes={artifact.path.name: artifact.sha256})
print(toolkit.query(root))
print(toolkit.query(root, "--owns", str(root.resolve() / "etc/example.conf")))
```

The small native seed helper calls genuine libalpm load/prepare/commit APIs.
Dependency checks, conflict handling, configuration preservation and the
installed-file database all belong to libalpm. It disables both install
scriptlets and transaction hooks using `ALPM_TRANS_FLAG_NOSCRIPTLET` and
`ALPM_TRANS_FLAG_NOHOOKS`; ordinary setup is deferred until the target userspace
can execute. It never fetches URLs. It is only enabled by the development
toolkit and must not be used to bypass signatures for public updates.

An unprivileged development transaction uses `fakeroot`. The installed package
database and archive metadata record intended ownership, but fakeroot does not
leave real root ownership on files after it exits. Image composition must
preserve fakeroot state through archive generation or apply the package-owned
numeric metadata in a privileged disposable build environment. Native file
integrity/owner checks on the raw unprivileged work tree cannot qualify a final
installed OS.

`toolkit.create_repository(directory, package_paths)` runs source-built
`repo-add` and creates a standard local database, accompanying file database,
package copies and a development snapshot manifest. It refuses an existing
database and duplicate package names. Use a fresh candidate directory. This API
does not promote stable/testing channels or sign public artifacts.

## Source-built guest package manager

The archive, curl, GnuPG/GPGME and `pacman` recipes cross-compile
against the validated source-built glibc and their own declared dependencies.
The coordinator composes a separate dependency sysroot for each recipe using
libalpm, then supplies the bootstrap headers. None of these recipes installs
libraries into the host or the shared bootstrap root. Target compiler sysroot
arguments and pkg-config search paths are explicit. Each build checks that ELF
payloads have no embedded library search paths and that their required shared
libraries exist in its staging area or dependency sysroot.

The archive dependency chain is glibc → xz/zlib/OpenSSL → libarchive → pacman;
curl and GPGME add the separately declared download/signature closure. The
BusyBox shell supplies the guest's scriptlet and local transport commands.
libarchive configuration must detect source-built gzip, XZ and OpenSSL support;
silently building an archive library without gzip would break package `.MTREE`
reading. Host Python, make, shell, Perl, Meson, Ninja and pkg-config are build
seeds. The resulting target ELF programs run through this distro's own loader
for initial probes; QEMU boot and transactions remain necessary runtime checks.

curl and libarchive revision 2 keep installed SDK flags portable. Their
pkg-config private dependencies use `${libdir}` rather than a temporary build
sysroot. `curl-config --cc` accepts the consumer's `CC` or reports `cc`; its
configure output contains portable options, while the exact original build is
retained in the compilation receipt. Curl's full static library is disabled and
`--static-libs` explicitly rejects that capability. The metadata-only exports
preserve every unrelated compiled payload byte and its original compilation
identity. A genuine fully static libarchive consumer and a libcurl consumer using
`pkg-config --static` flags were linked with audited source-built inputs and run;
the latter resolved all DSOs through the target loader with its cache disabled.
Evidence is in `out/qualification/sdk-consumers/<identity>/report.json`.

Guest pacman 7.1.0 revision 2 enables source-built curl and GPGME. Its package
defaults require signatures for repository databases, packages and direct local
installation. The ordinary package includes no release keyring; public update
deployment still requires maintained release trust and lifecycle policy.
Experimental console images explicitly apply `unsigned-local-development`
policy with `SigLevel = Never` and a file-only local transport. That generated
image choice does not weaken the package's strict default. The signed guest
fixture provisions only ephemeral public verification material and exercises
real ALPM acceptance/rejection. See [Authenticated updates](authenticated-updates.md)
for the complete target stack, TLS checks and genuine QEMU evidence.

The guest package includes pacman, libalpm, pacman-conf and vercmp. Bash-dependent
makepkg, repo-add, pacman-key and database-migration scripts are omitted until
their target dependencies are packaged. Repositories are currently created by
the isolated native toolkit. The upstream `testpkg` archive checker is retained.
The OpenSSL Perl rehash wrapper is also omitted;
the OpenSSL CLI supplies `openssl rehash` without requiring guest Perl.

`packaging.guest_test.prepare_upgrade_fixture(candidate_directory, toolkit)`
creates a package at revision 1 and a real repo-add repository containing
revision 2. Image composition installs `initial_package` with the target stack,
then calls `stage_upgrade_fixture(root, fixture, toolkit)` to seed an edited
administrator config and the private repository. The package-owned
`/usr/libexec/custom-distro/check-package-upgrade` runs inside the booted guest.
It uses `pacman -Syu`, checks the new package version and file ownership, and
asserts that the edited config survives while the new default becomes `.pacnew`.
Only a successful run prints `CUSTOM_PACKAGE_UPGRADE_OK`. This is a disposable
local update test, not evidence of signed or public network updates.

## Verification

```sh
python3 -m unittest discover -s tests -p 'test_packag*.py' -v
python3 -m unittest discover -s tests -p 'test_target_package_manager.py' -v
```

Archive tests exercise deterministic export, ownership, invalid backups,
immutable identity collisions, malicious archive paths and payload tampering.
The native integration tests, when the toolkit is available, exercise actual
pacman archive reading; libalpm dependency rejection, installation, ownership
queries and upgrade; edited administrator config preservation plus `.pacnew`;
removal plus `.pacsave`; suppression of package setup; and standard `repo-add`
metadata. A missing toolkit explicitly skips integration tests rather than
substituting mock transactions.

When source-built target packages exist, the target integration test runs the
guest pacman ELF through its own glibc loader and libraries to read a database
assembled by native libalpm, query ownership and read the exported upgrade
archive. This checks native/target format interoperability without implying a
QEMU boot or an in-guest transaction occurred.

Final public updates must also start from prior supported signed images,
upgrade using their configured repository and trust, verify data/services and
reboot. The fixture tests alone do not qualify boot, lifecycle migration,
power-loss handling, signed updates or whole-system rollback.
