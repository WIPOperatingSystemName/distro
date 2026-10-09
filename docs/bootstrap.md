# Source bootstrap and first console

The implemented bootstrap creates an x86_64 GNU/Linux cross compiler and GNU C
library from upstream source archives. It uses the compiler passes explained in
[Linux From Scratch 12.4 systemd](https://www.linuxfromscratch.org/lfs/view/12.4-systemd/).
The first image uses a static BusyBox console and an EFI-stub Linux kernel. It
is the smallest build profile; the full source-built systemd/Telorgon desktop
has separate guest qualification. This project does not claim a self-hosted
toolchain or public-release security qualification.

## Roots and source identity

The existing host supplies the initial compiler, linker, Python, shell, Make,
Bison, M4, Perl, archive utilities, and core command-line tools. These are declared
bootstrap seeds; their installed filesystem is never copied into the target OS.
Native Gawk, Flex and elfutils libelf are built from source into the project
because the target bootstrap and kernel generators require them. Native libelf
uses the host zlib development header/library as a declared generator seed; this
library never enters target packages. The host also supplies bc and pkg-config.
The bootstrap additionally stages source-pinned Meson 1.9.2 into its private
native prefix because the desktop sources require a newer version than some
host environments provide. Package builds verify its module receipts and record
the selected Meson path, version and source identity. It is a native build tool
and is excluded from the target OS.

All writable build directories remain under this project:

| Directory | Purpose |
| --- | --- |
| `out/sources/downloads` | Flat verified cache of upstream source archives |
| `out/bootstrap/work` | Extracted sources, build trees, stage logs and completion stamps |
| `out/bootstrap/root` | Source-built glibc sysroot and Linux API headers |
| `out/bootstrap/root/tools` | Native-executing cross compiler and Binutils |
| `out/bootstrap/root/tools/native` | Source-built native generator tools |
| `out/work/<package>` | Recipe-specific work and empty package staging trees |
| `out/packages` | Standard package archives and provenance |

`bootstrap/sources.lock.toml` pins the actual archive bytes with SHA256. GCC is
also checked against its upstream SHA512. LFS reference MD5 values provide an
additional comparison for the initially selected archives; they are not a
replacement for the SHA256 integrity pins. Binutils, BusyBox and Linux checksum
references are recorded beside their source identities.

Bootstrap stamps use schema 2. They record the runner/helper identities, declared
native command/compiler inputs, source pins, predecessor identities, and receipts
for each stage's actual files, symbolic links and modes. A cached stage must
match those receipts and its current input closure; an isolated stage rejects a
stale prerequisite instead of silently adopting it. Source changes start with
fresh extracted sources and build objects. This does not identify every byte of
the host environment or establish reproducible builds by itself.

The initial local builds predated these receipts. Their stamps were explicitly
migrated with:

```sh
python3 bootstrap/run-stage.py --sources out/sources/downloads --upgrade-stamps
```

Those records have `origin = legacy-current-baseline`: they establish the current
artifact baseline and rerun the libc/C++ execution probes, while explicitly
preserving that historical output/seed integrity was not established. Fresh
builds use `origin = built-with-receipts`. A receipt-only runner revision can
refresh an adopted baseline through the same explicit command; changed source,
build scripts, helpers, seeds, or predecessor artifact bytes require rebuilding.
This mechanism never relabels a completed build as having used new build inputs.

Package builds can run the checker before accepting the compiler or any cached
target artifact:

```sh
python3 -B bootstrap/run-stage.py --sources out/sources/downloads \
  --check --stage validate --stage gcc-pass2
```

It checks both selected stages and their complete prerequisite closure, verifies
cached archive bytes, returns `CUSTOM_BOOTSTRAP_CHECK_OK` on success, and never
compiles or updates completion stamps. Omit the second selected stage before
the second compiler has been built. `python3 build.py bootstrap --check` verifies
all stages; `python3 build.py bootstrap --upgrade-stamps` exposes the explicit
baseline migration through the coordinator. Missing or stale receipts fail the
check rather than creating output directories or adopting a new baseline.

This source set is an experimental, pinned starting point. Updating it
requires reviewing source pins, compatible toolchain inputs, and the affected
build closure. Do not treat these fixed versions as a current security-supported
release merely because their first build succeeds.

## Implemented stages

| Stage | Result |
| --- | --- |
| `binutils` | Target assembler, linker and binary inspection tools with the explicit target sysroot |
| `gawk-native` | Native Gawk used by configure/build generators |
| `flex-native` | Native Flex used by Linux Kconfig generators |
| `elfutils-native` | Native libelf used by Linux objtool, with private include/link paths |
| `meson-native` | Source-pinned private Meson for target package and native generator builds |
| `gcc` | Temporary C cross compiler; source-built GMP, MPFR and MPC are compiled into the compiler build |
| `headers` | Sanitized Linux userspace API headers installed into the target sysroot |
| `glibc` | Cross-built glibc, static library, runtime libraries and target dynamic loader |
| `validate` | Header/linkage isolation checks and a probe executed using the newly built target loader and libc |
| `gcc-pass2` | Separate cross C/C++ compiler with shared target runtimes and an exception/iostream execution probe |

The target triplet is `x86_64-custom-linux-gnu`. The first compiler supports C.
The second pass installs into `tools/pass2`, preserving the first compiler and
libc while other packages build. Its probe uses the source-built target loader,
glibc, libgcc_s and libstdc++ and rejects linkage outside target inputs. Rust and
a full native self-hosted compiler remain separate components.
Target libraries use `/usr/lib`, with the required `/lib64` loader compatibility
link. GCC's native build helpers may use host libraries; its target executable
headers and libraries must resolve inside this project's sysroot/tools.

The validation stage checks the ELF interpreter, header search paths, linker
inputs and absence of unexpected runtime search paths. It invokes the explicit
source-built loader with the explicit source-built library path and expects
`CUSTOM_LIBC_OK 2.42`. A successful stamp alone must never stand in for these
checks.

## Running the first build

From the project directory:

```sh
python3 build.py doctor
python3 build.py validate
python3 build.py fetch --bootstrap
python3 build.py bootstrap --jobs 12
python3 build.py native-toolkit --fetch --jobs 4
python3 build.py build linux pacman --jobs 12
python3 build.py loader
python3 build.py image
python3 build.py vm
```

The download command needs network access once. Subsequent builds use verified
cached archives. The loader builds a recorded snapshot of the pinned Telorgon
projects and its pinned UEFI Rust compiler; it does not modify those projects.
See the build report for the exact source/toolchain identity and the VM report
for actual runtime evidence.

An individual bootstrap stage can be invoked for diagnosis:

```sh
python3 bootstrap/run-stage.py --stage glibc \
  --sources out/sources/downloads --jobs 12
```

That stage requires completed predecessor stages. Its log is
`out/bootstrap/work/glibc/build.log`. Running the full bootstrap checks completion
state in dependency order. `--force` rebuilds a selected stage; force rebuilding
prerequisites requires checking/rebuilding their consumers before images are
accepted.

The first verified second pass provides source-built shared `libgcc_s` and
`libstdc++`; the exception/iostream probe prints `CUSTOM_CXX_OK` through the
source-built glibc loader. It checks canonical header/linker paths, required
runtime dependencies and absence of unexpected runtime search paths. This is a
native-executing cross compiler, not a native compiler running inside the distro.

For development parallelism only, `python3 build.py build linux --seed` can
compile the upstream kernel using the declared host compiler. The kernel is
freestanding and does not link host userspace libraries, and the artifact report
records seed mode. The normal command above compiles it using this project's
cross compiler. BusyBox and glibc deliberately reject seed mode.
Target builds with package dependencies receive a private sysroot. BusyBox
passes that path through its compilation and final link and verifies that its
selected static libc resides there.

## Package handoff

The `glibc` recipe exports the runtime from the completed, validated source
bootstrap. It excludes bootstrap tools and does not copy any host runtime
libraries. The `busybox` recipe links statically against this glibc, enables
the required shell/root-mount utilities, and rejects any ELF interpreter or
shared-library dependency. BusyBox's config is shipped with the package.
The `libgcc` and `libstdc++` recipes export target libraries from the validated
second pass into `/usr/lib`; the latter includes its target C++ headers and
development archives. They never export the native-executing cross compiler.

The `linux` recipe starts from a small explicit config, enables EFI stub,
initramfs decompression, serial console, devtmpfs, GPT/ext4 and VM storage
support, and verifies required resolved configuration symbols. It stages the
kernel as `boot/vmlinuz-6.12.57-custom`, its resolved config, and
`boot/kernel-build.json`. Physical hardware support remains subject to separate
qualification.

The coordinator composes package-owned files and the recorded first-console init
scripts into boot media. The tools subtree of the bootstrap sysroot is not an
OS package and must never enter that media. A serial success marker establishes
only its implemented console milestone. A persistent ext4 root, local and strict
signed package upgrades, and the systemd/PAM/logind session have separate passing
guest receipts. A disposable password fixture has also passed locked/wrong
password rejection, genuine normal-user authentication and exact shadow
restoration. The Telorgon desktop has its own window/presentation/input checks.
An installer, recovery and interactive account enrollment remain separate gates;
see the corresponding runtime documents and exact-image reports.

Source archives, patch/recipe inputs and build trees are retained for provenance.
Before distributing static BusyBox linked with LGPL glibc, release tooling must
also preserve the applicable source/object and relinking materials required by
the component licenses.
