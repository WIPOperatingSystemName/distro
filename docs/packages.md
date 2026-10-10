# Packages and ALPM transactions

The Python builder compiles this distro's target software and exports standard
ALPM packages. Source-built pacman/libalpm owns dependency checks, installation,
upgrades, file ownership and the installed database. The format does not import
Arch packages or another distro's filesystem.

## Build and dependency model

Ordinary recipes live in `packages/<name>/package.toml`; Telorgon application
recipes use `application.toml`. Record immutable source pins, version/revision,
license notices and runtime requirements. Use the Python CLI:

```sh
python3 build.py validate
python3 build.py plan pacman
python3 build.py affected glibc
python3 build.py fetch pacman
python3 build.py build pacman --jobs 4
```

These individual stages assume the bootstrap and private toolkit are ready.
For a complete initial environment, use `./run` as described in
[build and run](build.md), rather than assembling the stages by hand.

`native` and `target` dependencies determine compile ordering. Target packages
receive a private ALPM-composed compile sysroot and validated cross compiler.
`runtime` dependencies enter the installed image closure. Native generators
use declared seeds or private source builds and must not enter the target root.
Image assembly selects systemd's libudev provider instead of conflicting eudev.

ELF audits reject unintended host interpreters/library paths, missing target
DSOs and unapproved RPATH/RUNPATH. Probes use the explicit target loader and
libraries. API checks on the host kernel still require separate guest tests.

## Archive contract

The exporter writes deterministic `.pkg.tar.xz` archives with `.PKGINFO`,
`.BUILDINFO` and gzip-compressed `.MTREE`, retaining runtime dependencies,
provides/conflicts/replacements, backup paths, numeric ownership, modes and
symlinks. Package stages define complete payloads; split packages need separate
stages. Source pins and exact versions live in the recipes, not duplicated
version tables in the docs.

`distro_build.packaging.export_package` exports a prepared stage.
`inspect_package` checks archives without extracting them, including metadata,
member traversal/duplicates, symlink containment and payload/MTREE agreement.
Regular files, directories and symlinks are supported. Device nodes, FIFOs,
sockets, imported hardlinks and unqualified xattrs/capabilities are rejected.
Runtime devices normally come from guest device management.

The adjacent `.build.json` retains compilation provenance; `.BUILDINFO` binds
the actual packaging descriptor. Build identities include source, recipe,
helper, dependency, compiler and sysroot inputs. Equal supported inputs and
payloads produce equal archive bytes. Changing published payloads or metadata
requires a new version/revision: the exporter rejects different bytes under the
same name/version/architecture.

## Assembly toolkit and guest pacman

The private native toolkit under `out/native-toolkit/` is built from pinned
upstream sources with declared host compiler/libc inputs. Its local bootstrap
transactions use real libalpm, verify package digests and suppress target
scriptlets/hooks on the host. It is an assembly tool, not an OS package.
Use `python3 build.py native-toolkit --fetch --jobs 4` to prepare it when
working on an individual stage.

The guest pacman package is cross-built against this distro's libc with the
source-built curl/GPGME/GnuPG dependency stack. Its package defaults require
signatures. The private development image explicitly records its unsigned
local-development trust policy; this does not supply public update trust.
Development deployment verifies the transfer and uses one guest pacman
transaction. It never replaces executables outside the package database.

The guest includes pacman, libalpm, pacman-conf, vercmp and the upstream archive
checker. Bash-dependent makepkg/repository/key-management scripts require a
separate target dependency profile. Local fixture repositories are created by
the isolated native toolkit. Neither unsigned fixtures nor a successful signed
fixture qualify a public update channel.

## Application deployment packages

`build.py deploy` snapshots the edited source and builds selected apps against
the recorded SDK. Content-specific `.dev<build-identity>` versions and separate
receipts under `out/state/apps-development/` preserve immutable package bytes
without replacing the normal image-composition artifacts.

The four package names are `telorgon-shell`, `telorgon-file-explorer`,
`telorgon-settings-app` and `telorgon-portal-picker`. A framework renderer change
requires rebuilding all four. [The deployment guide](build.md#deploy-applications-into-a-running-development-vm)
covers installation, restarts and actual interaction testing.

## Verification and release trust

Archive and integration checks are in `tests/test_packag*.py` and
`tests/test_target_package_manager.py`. Native tests exercise real transactions,
dependency rejection, ownership, configuration preservation, `.pacnew`,
`.pacsave` and repository metadata when the toolkit exists. Guest upgrade tests
must additionally boot the exact image and run target pacman.

[Testing](testing.md#package-upgrades-and-signatures) describes unsigned and
strict signed guest fixtures, including negative verification cases.
A supported release additionally needs maintained public trust, signed immutable
packages/databases/manifests, previous-version upgrade/reboot tests, key rotation
and recovery. Keep release private keys outside recipes, images, build workers,
logs and prompts; see [release requirements](release.md).
