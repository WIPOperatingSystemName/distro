# Authenticated update stack

The target package manager is pacman 7.1.0 revision 2, built with both curl and
GPGME. Its default package configuration requires signatures for repository
databases and packages, including direct local package installation. There is
no signing key or trust keyring in the ordinary pacman package. A real release
must provision its public release keys before installing repository updates.

The source-built dependency stack is:

| Component | Target version | Role |
| --- | --- | --- |
| curl | 8.22.0 | HTTPS/file repository and application transfers |
| CA certificates | 2026.09.25 | Pinned Mozilla root data from curl's extraction service |
| libgpg-error | 1.61 | GnuPG error/configuration API |
| libassuan | 3.0.2 | GnuPG local IPC API |
| libgcrypt | 1.12.4 | OpenPGP cryptographic primitives |
| libksba | 1.8.1 | Certificate handling |
| npth | 1.8 | GnuPG thread support |
| GnuPG | 2.5.24 | Target gpg/gpgv/gpgconf/gpg-agent engines |
| GPGME | 2.2.0 | ALPM signature verification API |

The curl source archive checksum was checked against its upstream release and
its detached signature was verified against the fingerprint published in
[curl's verification instructions](https://curl.se/docs/verify.html). GnuPG
component hashes were checked against the upstream
[integrity list](https://www.gnupg.org/download/integrity_check.html), and their
detached signatures verified against published
[maintainer fingerprints](https://www.gnupg.org/signature_key.html). The public
upstream keys and verification receipt are under `out/source-verification`.
Fetching these keys over HTTPS and comparing published fingerprints is the
documented source-verification trust bootstrap; it is separate from the
distribution's eventual release-key policy.

The CA bundle is a pinned source file, not the build machine's certificate
store. Its SHA-256 matches the upstream companion checksum. The conversion to
PEM does not preserve Mozilla name constraints; the package notice records
that upstream limitation. The package retains the MPL-2.0 license.

Each library uses a private dependency sysroot and the source-built target
compiler. ELF checks reject RPATH/RUNPATH and unresolved shared libraries. The
target loader's library resolution is checked before host-kernel runtime
probes, with the loader cache disabled and explicit source-built library paths.
GPGME's engine path is `/usr/bin`, so a guest must contain the packaged GnuPG
engine rather than relying on a host executable.

`packaging.network_test.qualify_curl` installs the exact curl dependency closure
and exercises an actual TLS connection. It records successful transfer with
the test certificate, curl error 60 for an untrusted certificate, and error 60
for an incorrect hostname. The loopback server uses host Python as a declared
test fixture; curl, OpenSSL, libc and the default CA bundle are target-built.
Private test TLS keys stay below `out/network-tests` and never enter an image.

`packaging.signed_test.prepare_signed_fixture` creates a disposable local
repository and a short-lived test key below `out/signing-tests`. It records
the host gpg fixture generator's version and executable hash, signs the test
database/packages, then deletes the private signer and stops only its private
agent. The image receives public verification material only. The package-owned
guest check exercises:

- A normal signed ALPM upgrade from revision 1 to 2, including file ownership,
  preserved administrator configuration and the expected `.pacnew` file.
- An unsigned package rejected by required-signature policy.
- A changed package rejected by OpenPGP verification. Its changed archive's
  checksum deliberately matches the correctly signed database, while its
  original detached signature is invalid, separating this from a digest test.
- A repository database rejected after its required signature is removed.

The guest must emit `CUSTOM_SIGNED_PACKAGE_UPGRADE_OK` only after every check
passes. Fixture preparation or a version command does not establish guest
ALPM success. `packaging.signature_test.qualify_signatures` additionally uses
the exact target GnuPG/libgcrypt closure to verify the signed package/database
and reject the changed package, without an agent or host engine.

The complete guest integration check passed in QEMU on 2026-10-08. The
[result receipt](../out/verification/signed-upgrade-final/result.json) records
`success: true`, the completed `CUSTOM_SIGNED_PACKAGE_UPGRADE_OK` line, and image
SHA-256 `db7bde36c102460fbefc0b3f0ed9a734eff4c6dd7b4c6a8da60c68892a69e497`.
The [serial log](../out/verification/signed-upgrade-final/serial.log) shows the
accepted signed upgrade, unsigned package rejection through both repository
installation and direct `-U`, invalid OpenPGP rejection of the changed package,
and rejection of the missing repository database signature. The first trial's
marker matcher mistakenly accepted a marker quoted inside manifest output;
that trial is superseded and is not evidence. The final check uses completed
standalone serial lines and the corrected negative-check assertions.

The experimental unsigned console remains an explicit development choice.
`configure_development(root)` writes `SigLevel = Never` only in generated
project image roots, and returns `trust_policy = unsigned-local-development`
for the image manifest. It does not disable compiled curl/GPGME support or
change the default pacman package. Never use that policy for published updates.

The minimum GnuPG profile disables key servers, keyboxd, smart cards, TPM,
network certificate lookup and optional TLS stacks. The minimum curl profile
uses OpenSSL and zlib; HTTP/2, HTTP/3, IDN, PSL, SSH and optional compression
backends are not included. Cross-build upstream tests that require executing
engines during configure are disabled; runtime qualifications above are
separate evidence.

Run the private host-kernel qualifications after building the canonical target
packages and native assembly toolkit:

```sh
python3 tools/qualify-network-crypto.py tls signatures
```

The tool writes fresh public verification receipts below `out/`. Its TLS
listener and temporary private signing agent require a worker that permits
private socket binding. For actual ALPM verification, compose and boot the
signed integration-test image; this helper does not claim guest ALPM success.

This is update verification infrastructure, not a public release-signing
service. Release-key protection, key rotation/revocation, offline recovery
trust, channel authorization and signed release manifests still need explicit
release policy. AI-assisted recipe changes must not gain access to release
private keys merely because their build or integration checks pass.
