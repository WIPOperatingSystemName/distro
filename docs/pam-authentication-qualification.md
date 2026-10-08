# Guest password authentication qualification

The disposable QEMU/KVM guest passed the real password authentication gate.
[`out/verification/pam-authentication-marker/result.json`](../out/verification/pam-authentication-marker/result.json)
records `success: true`, the completed serial marker
`CUSTOM_PAM_AUTHENTICATION_OK`, and image SHA-256
`262dac7958e49f8fddd346208541ed5385ca3fcd26f409f1bb6800100104131a`.
The [serial log](../out/verification/pam-authentication-marker/serial.log)
records both the locked-default and wrong-password attempts returning
`PAM_AUTH_ERR` (7), followed by the correct-password attempt returning
`PAM_SUCCESS` (0) from both `pam_authenticate` and `pam_acct_mgmt`. Each probe
ran as UID 1000 and received a real password conversation prompt. The final
marker is emitted only after the original locked `/etc/shadow` contents have
been restored and their whole-file SHA-256 verified.

This qualifies the source-built PAM/password-helper path in that disposable
guest. Ordinary image accounts remain locked. Interactive console or GUI login,
installer/account enrollment and a GUI polkit authentication agent remain
separate work.

`tools/build-pam-auth-probe.py` compiles `tools/pam-auth-probe.c` with the
validated pass2 compiler, verified glibc/libxcrypt/PAM package archives and
individually recorded bootstrap headers. It audits actual linker inputs,
interpreter and own-loader library resolution. The local self-test exercises the
password conversation; an attempted authentication invocation is rejected before
calling PAM because the host lacks the explicit private guest marker.
Compilation and that self-test do not qualify guest password authentication.

The current compiled probe is described by
`out/qualification/pam-auth-probe/current.json`. Its report contains exact SDK
package identities, source/compiler/header digests, loaded-library bytes and the
PAM archive's root-owned setuid `unix_chkpwd` entry. The probe is a qualification
tool, not an OS package or default authentication policy.

For a disposable private VM fixture, invoke the builder with `--make-fixture`.
It creates a mode 0700 private fixture directory containing mode 0600 `password`,
`wrong-password` and `password.hash` files. The random password is hashed by the
compiled target using the source-built libxcrypt library and a fresh salt, using
SHA-512 with 100000 rounds. Neither password nor hash is printed. This step does
not enroll an account or edit an image. Fixture files must stay out of ordinary
release images and publication artifacts.

The verified image/VM gate runs three ordered checks in an explicit disposable
guest fixture:

1. Confirm the default `custom` shadow field is locked, then feed the generated
   password to `pam-auth-probe --expect-deny custom` as UID 1000. Require
   `CUSTOM_PAM_PASSWORD_REJECT_OK`.
2. In that guest fixture only, enroll the generated hash for `custom`, preserving
   every other account and the intended account-aging fields. Feed
   `wrong-password` to the same denial check. Require the same rejection marker.
3. Feed the generated `password` to
   `pam-auth-probe --expect-success custom` as UID 1000. Require
   `CUSTOM_PAM_PASSWORD_LOGIN_OK`, which also requires successful account checks.

Create the root-owned, readable file
`/run/custom-distro/pam-auth-qualification` with exactly
`disposable-qemu-qualification` followed by a newline. This explicit marker is
required before the guest authentication mode can run. Root can supply stdin via
a transient systemd service with `User=custom`, `Group=custom` and
`StandardInput=file:/home/custom/.local/state/pam-test/<fixture-file>`;
credentials need not appear in command arguments, environment variables or
console logs.

The probe requires real and effective UID 1000, the matching local account,
unreadable `/etc/shadow`, a root-owned setuid shadow helper, a filesystem allowing
setuid execution and `NoNewPrivileges=0`. It checks that the root-owned `login`
policy has exactly one `auth required pam_unix.so` and one
`account required pam_unix.so`. Password input comes only from stdin. A denial
counts only as `PAM_AUTH_ERR` with a real password prompt; module/configuration
errors cannot produce a success marker. The positive fixture check is essential
to distinguish genuine password rejection from a broken privileged helper.

The harness calls `pam_authenticate` and `pam_acct_mgmt`. It does not open a PAM
session, enroll users, change PAM policy or test interactive util-linux/GUI login.
Actual logind session creation and password authentication are separate guest
gates. The ordinary profile keeps its locked defaults; installer enrollment and
interactive authentication need their own qualification.

## Disposable ALPM fixture package

`distro_build.packaging.pam_test.prepare_pam_fixture(directory, toolkit)` exports
the private `custom-distro-pam-test` package. Its immutable version includes the
fixture identity, which binds the actual probe binary, probe report, qualifier
source, builder, fixture module/script/unit and credential digests. Provenance
contains only credential hashes. The package archive is mode 0600 inside a mode
0700 output directory because it contains disposable credential payloads.
The generated fixture image object and its image alias are also mode 0600.

Install `initial_package` in the generated root's real ALPM assembly transaction,
then call `stage_pam_fixture(root, fixture, toolkit)`. Staging verifies installed
SDK versions and target PAM/helper/library bytes, requires the initially locked
`custom` shadow entry and binds that exact entry's digest. It never unlocks or
enrolls an account. The payload's mode 0600 credential files live under
`/home/custom/.local/state/pam-test`; the image's numeric ownership policy must
assign the normal home tree UID/GID 1000.

The package installs `custom-distro-pam-authentication-test.service`, requiring
and ordering after `dbus.service`, before `custom-distro-system-check.service`.
The root oneshot runs the three actual PAM cases using transient systemd services
with normal UID/GID 1000 and file-backed stdin. Each transient probe writes to a
private output file which root reads after `--wait` finishes; no journal lookup
is needed. The root-owned `/run/custom-distro` parent is mode 0755 so the normal
user can read the nonsecret qualification marker; the backup/output child stays
mode 0700. A failed precondition reports only its name, never credentials.
Passwords and the enrollment hash are never placed in command
arguments or environment variables. The original shadow file is backed up
privately, restored on EXIT/HUP/INT/TERM and verified before success. Only after
all three cases and restoration does it emit `CUSTOM_PAM_AUTHENTICATION_OK` and
create `/run/custom-distro/pam-authentication-ok`; errors emit
`CUSTOM_PAM_AUTHENTICATION_FAILED`.
