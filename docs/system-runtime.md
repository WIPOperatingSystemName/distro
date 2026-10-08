# System services and authentication

The system runtime is built from upstream source with the distro's validated
compiler and C library. The recipes stage package files in a private directory;
they do not install or activate services on the build host.

| Package | Version | Purpose |
| --- | --- | --- |
| libxcrypt | 4.5.2 | Password hashing for PAM and login tools |
| libcap | 2.78 | Linux capability API |
| libseccomp | 2.6.1 | System-call filter construction and systemd service hardening |
| pam | 1.7.3 | Authentication, account, password and session modules |
| util-linux | 2.42.4 | Login/getty and mount, block-device and namespace tools/libraries |
| libsystemd | 259.9 | Independent public sd-bus, socket activation and other systemd APIs/headers |
| systemd | 259.9 | PID 1, journald, udev, logind and PAM session integration |

The systemd recipe provides `libudev` and conflicts with the initial eudev
`libudev` package. A systemd image must select one provider. Installing both is
an error; switching to systemd also switches its udev daemon, rules and library.
The Telorgon bootloader remains the bootloader. This recipe disables
systemd-boot, ukify and systemd's EFI integration.

The first systemd slice includes logind, sysusers, tmpfiles, udev, hwdb, PAM and
seccomp. Networkd, resolved, timesyncd, homed, hibernation and optional import or
container image managers are disabled. The ldconfig and quotacheck services,
binfmt registration and namespace resource daemon are also disabled until their
separate tool and policy requirements are packaged. D-Bus comes from the separate canonical
`dbus` recipe; disabling systemd's optional libdbus dependency does not disable
its own sd-bus implementation or remove the runtime system-bus requirement.

The public `libsystemd.so`, headers and pkg-config metadata belong to the
independent `libsystemd` package. Both it and systemd compile the pinned upstream
source using declared target dependencies. The library has no D-Bus runtime
dependency, so D-Bus can enable genuine socket activation without a package graph
cycle. Systemd revision 2 removes those public files and depends on this library;
the private `/usr/lib/systemd` libraries remain owned by the service manager.

Systemd revision 3 compiles its upstream polkit protocol integration and declares
the genuine `polkit` authority as a runtime dependency. This does not require
linking systemd against libpolkit. Policy files stay in
`/usr/share/polkit-1/actions`; the build requires the upstream manage-units
defaults `auth_admin` for arbitrary/inactive callers and `auth_admin_keep` for
active sessions. The builder records `ENABLE_POLKIT=1` and installed policy byte
digests in its `polkit-feature.json` receipt. It rejects temporary build paths
inside policy files. No custom authorization allow rule is installed by this
recipe. Guest authorization decisions require their own QEMU checks.

The current canonical revision 3 archive has SHA-256
`cccce4c50af91c2ee71fdd3549a8e25c39e25aef87244f0fc83f9e8fa83111a8`.
Its build passed own-loader PID 1 feature checks, ELF dependency/path auditing,
and the polkit configuration/policy check. The archive ownership comparison
against the independent libsystemd and polkit packages found no overlapping
non-directory paths (`out/verification/systemd-polkit-split.json`).

## Kernel contract

Linux package revision 3 requires every explicitly enabled Kconfig symbol to
remain enabled after `olddefconfig`, before compiling. This covers the existing
EFI/DRM/input drivers and the service/session prerequisites, including shmem,
tmpfs with ACLs and xattrs, all requested namespaces, seccomp, audit/loginuid,
keyrings and cgroup controllers. FUSE is built in for desktop document portals;
its userspace mount helper and permissions are separate package/image inputs.

`/boot/kernel-build.json` records the actual compiler, image/config/vmlinux
SHA-256 digests and the required/disabled configuration. The builder checks the
kernel ELF architecture and rejects userspace interpreter/library requirements.
The compressed EFI image and config are packaged together. These build checks
complement the actual guest tests; they do not qualify physical hardware.

## Build inputs

Each recipe has a fixed upstream archive URL and SHA-256 digest. Linux-PAM,
libxcrypt and libseccomp digests match upstream release-asset metadata;
libcap and util-linux match the upstream checksum lists. The systemd source
archive is pinned to the upstream `v259.9` tag and its downloaded SHA-256.
These checks do not claim that a maintainer signature was verified locally.

GNU gperf 3.1 is a separate native bootstrap stage with an artifact receipt.
Jinja2 is a declared host Python generator for systemd. Generated files are
target inputs; generator executables and Python module bytes belong in build
provenance and cache identity, rather than being copied into the guest.

Target compiler wrappers use a private package sysroot. ELF audits reject
unprovided shared libraries, host interpreters and embedded host paths. Systemd
uses its upstream private library directory, `/usr/lib/systemd`, as its only
permitted embedded guest library path.

## Authentication policy

PAM installs `pam_unix`, its root-owned setuid shadow-password helper, and a
default `other` policy that denies all four PAM operations. An image profile
must add explicit policy for login and the intended user sessions. A successful
package build does not establish a usable password login or a logind session.
The packaged PAM, systemd and udev configuration files use pacman's backup
metadata so package upgrades preserve local administrator changes.

The PAM build probe loads the source-built deny and permit modules from a
private work-directory configuration. The permit configuration is a test
fixture and is never installed in the OS. The crypt probe distinguishes two
password hashes. Those build-time checks exercise the APIs. The independent
guest password authentication gate below verifies the actual normal-user
`pam_unix` and privileged shadow-helper path.

## Guest acceptance gates

`out/verification/systemd-regular-user/result.json` records a passing QEMU run
against image SHA-256
`a1fadb2977951701b94ca897a747932562461e06c40189b43548a86b0681dcab`.
PID 1, journald, udev and socket-activated D-Bus were active. The genuine
`pam_systemd` session belonged to UID 1000, was active on seat0, owned its 0700
runtime directory and home, and could write there. The actual systemd user
manager started. Default passwords remained locked; this gate exercises account
and session management rather than password authentication.

[`out/verification/pam-authentication-marker/result.json`](../out/verification/pam-authentication-marker/result.json)
records a separate passing QEMU/KVM password authentication gate against image
SHA-256 `262dac7958e49f8fddd346208541ed5385ca3fcd26f409f1bb6800100104131a`.
The [serial log](../out/verification/pam-authentication-marker/serial.log)
shows the genuine UID 1000 probe rejecting the initially locked account and an
incorrect password with `PAM_AUTH_ERR` (7), then accepting the disposable
correct password with both `pam_authenticate` and `pam_acct_mgmt` returning
`PAM_SUCCESS` (0). The root fixture emitted the completed
`CUSTOM_PAM_AUTHENTICATION_OK` marker only after restoring the original locked
shadow file and verifying its whole-file SHA-256. Credential input stayed in
private files and stdin; no password or enrolled hash was logged. See
[the password qualification procedure](pam-authentication-qualification.md).

These guest gates establish password checks and PAM/logind session creation
separately. Ordinary profile accounts remain locked. Interactive console/GUI
login, installer/account enrollment and a GUI polkit authentication agent still
require implementation and qualification.

Continue session/runtime qualification with private QEMU checks for:

1. systemd running as PID 1, with writable `/run`, mounted cgroup v2 and the
   required kernel namespace/seccomp features;
2. udev device enumeration and the system D-Bus with its messagebus account,
   machine ID, runtime directory and policy;
3. normal-user PAM password/account success and locked/wrong-password rejection
   (verified above), plus separate interactive login qualification;
4. a logind session, user runtime directory, system/session buses and logout
   cleanup;
5. the Telorgon desktop launched through that session with the expected device
   permissions (software-rendered window/input gate passed; see
   [desktop qualification](desktop-session-qualification.md));
6. a real service seccomp filter and negative syscall test inside the guest;
7. package installation and upgrade without conflicting udev providers.

The package builder's own-loader probes run unprivileged and do not load a
seccomp filter onto the build host. Guest results must be recorded separately
from compilation and API results.
