# Package changes and pull requests

Start with the [organization quickstart](https://github.com/WIPOperatingSystemName).
The [maintainer GitOps plan](collaboration-plan.md) covers CI, AI review, approved
merges and downloadable test candidates.

Keep a software change in one `packages/<name>/` directory. Start from a nearby
working recipe, record the upstream HTTPS archive and its verified SHA256, and
declare target libraries separately from native generators and runtime packages.
The build script installs into `CD_STAGE_DIR`; it never installs into `/usr` on
the build host. Include license notices and explicit preserved configuration paths.

The coordinator resolves dependencies, verifies source archives, composes the
target dependency sysroot through ALPM, records build inputs and exports a native
package archive. Use `python3 build.py plan <name>` before building and
`python3 build.py affected <name>` to see consumers that need qualification.
Run `validate`, the relevant archive/transaction checks, and an actual runtime
probe for changes to services, boot or updates. Compilation alone is insufficient.

`native` and `target` dependencies determine compile ordering; their cycles are
errors. A `runtime` dependency declares installed behavior and enters the complete
image closure. Runtime cycles are valid: for example, systemd and its polkit policy
can require each other after installation without requiring each other's binary
to compile. The private `out/work/<package>/<identity>/sysroot` contains the compile
closure. Its explicit libalpm mode omits runtime dependency checks but preserves
conflicts, ownership, package metadata and digest verification. Image assembly,
guest upgrades and ordinary installs retain complete dependency checks. When
systemd supplies the libudev API, the coordinator selects that provider rather than
installing conflicting eudev and systemd payloads together.

Current named recipes have empty `native` lists; native generators use declared
host seeds or source-pinned private helper builds. A future named native package
needs a separate build-host artifact namespace and generator path integration
before it can be populated here. A target executable cannot serve as an implicit
host-native dependency just because the CPU architectures happen to match.

Local Telorgon application changes remain in their existing source repositories.
The default inputs are the commit-pinned submodules under `sources/`; use
`--source-root` explicitly to build a separate development workspace.
`apps prepare` records the actual content, Cargo locks and small distro overlays
in a private source snapshot. It includes existing uncommitted work and never
mutates those checkouts. `apps check` reports missing target libraries and runtime
files. A successful native build still needs a graphical/session test.

PR CI uses read-only repository permissions and ordinary disposable hosted
workers. Untrusted recipes are executable code: run them only on workers without
signing keys, deployment credentials or access to a privileged persistent host.
The local trusted developer executor is not a security boundary for arbitrary
PRs. A shared build directory must not be writable by untrusted jobs.

AI can prepare small recipe updates, investigate logs, explain affected consumers,
and open reviewable changes. Source pins must come from verified upstream evidence;
do not accept invented hashes or replace another distro's binary packages with
an apparent source build. Keep AI away from release keys. CI results and runtime
evidence should be attached to the PR; a separate authorized release service
signs and promotes reviewed artifacts. Never let a PR's code run with release
credentials or auto-promote merely because an LLM judged it safe.

Changes to published payloads, dependency metadata or preserved configuration
defaults require a revision/version increment. Preserve immutable artifacts and
bind archive, source, recipe and dependency digests in the release manifest.
The upstream repository is `WIPOperatingSystemName/distro`. No remote CI run,
PR or public release has been created by these build commands.

## Replaying local sources in CI

`python3 build.py source-bundle export` writes a deterministic source archive and
its digest under `out/bundles/`. It includes the selected source, assets, license
inputs and Cargo locks for all Telorgon projects and the loader. It excludes
Git internals, host Cargo credentials, build outputs and private development logs.
Source bytes and executable bits are preserved; writable transport permission
bits are normalized for safe archive extraction and original modes are recorded.

The `Telorgon disk candidates and VM qualification` workflow defaults to a bundle
exported from the source submodules checked out at the parent repository's pins.
It also accepts an explicit HTTPS URL and reviewed SHA256 for a different bundle;
supply both inputs together. Its `console` profile uses an
ordinary hosted worker to rebuild the core source toolchain/packages and Telorgon
EFI, boot the disk, and perform unsigned development and strict signed positive
and negative guest upgrade tests. The `desktop` profile additionally builds the
complete pinned package catalog, constructs the target SDK, builds all Telorgon
applications from the imported bundle, and requires actual systemd/PAM and desktop
VM results. The desktop gate includes FileExplorer and Settings configured SHM
windows plus real focused keyboard input and a presented redraw, checked against
the image SHA recorded by the VM runner.

The full source build uses substantial temporary space. The desktop job requires
a freshly created disposable Linux x86_64 Debian/Ubuntu-style worker labelled
`custom-distro-ephemeral`, at least 128 GiB free disk and 16 GiB RAM, an available
Rustup seed. Bootstrap installs source-pinned Meson 1.9.2 under
`out/bootstrap/root/tools/native/bin/meson`; the host Meson version does not gate
target builds. CI checks the bootstrap receipts and saves the selected tool
paths, versions and provenance in `out/ci/native-inputs.json`. Its host seed
setup also installs CMake and libclang for actual application build generators.
Select 2, 4 or 8 compile jobs to fit the worker; 4 is the default. Destroy the worker
after the job. Never attach this label to a persistent privileged workstation or
a worker with signing keys or deployment credentials. The job runs only on explicit
manual dispatch, with read-only repository permissions and pinned GitHub actions.

The same desktop preparation can be replayed locally after importing the bundle:

```sh
python3 build.py validate
python3 build.py fetch --bootstrap
python3 build.py bootstrap --jobs 4
python3 build.py bootstrap --check
python3 build.py doctor
python3 build.py native-toolkit --fetch --jobs 4
python3 build.py build --jobs 4
python3 tools/compose-desktop-sdk.py
CD_SOURCE_WORKSPACE=$(python3 -c 'import json; print(json.load(open("out/bundles/current.json"))["root"])')
CD_DESKTOP_SYSROOT=$(python3 -c 'import json; print(json.load(open("out/sdk/current.json"))["sysroot"])')
python3 build.py apps prepare --source-root "$CD_SOURCE_WORKSPACE"
python3 build.py apps check --sysroot "$CD_DESKTOP_SYSROOT"
python3 build.py apps build --sysroot "$CD_DESKTOP_SYSROOT" --online --jobs 4
python3 build.py loader --source-root "$CD_SOURCE_WORKSPACE" --online
python3 tools/build-desktop-session-probe.py
python3 build.py image --profile desktop
python3 build.py vm --image out/images/custom-distro-desktop.img --expect CUSTOM_DESKTOP_SESSION_OK --desktop-input --output out/verification/desktop-final --timeout 150
python3 tools/check-wayland-window.py --vm-report out/verification/desktop-final/result.json --output out/verification/desktop-final/windows.json
```

The workflow builds the EFI loader and runs target API/TLS/OpenPGP/private IPC
qualifiers and console, signed upgrade and systemd VM gates before compiling the
desktop applications. A later app failure therefore retains the independent
runtime receipts. Its dedicated PAM
authentication gate compiles `tools/build-pam-auth-probe.py --make-fixture`, creates
a private `image --profile systemd --test-pam-auth`, and requires the completed
`CUSTOM_PAM_AUTHENTICATION_OK` guest assertion. That fixture verifies rejection
of the locked account and a wrong password, successful real `pam_unix` authentication
and account checks using a temporary random password, and restoration of the
original locked shadow entry before success. Passwords travel through private
stdin files, never command arguments or shell tracing. They remain isolated in
the disposable worker and guest; this gate does not enroll an actual user account.

The workflow uploads explicit public receipts, selected native tool reports and
application build logs, excluding raw disk images,
OS package archives, PAM image build manifests and credential directories, signing
key directories, private Cargo state and test TLS keys. The PAM probe and VM
receipts contain hashes, statuses and paths rather than credential values. Destroy
the fixture-bearing worker after the run. These dispatch definitions have not been run remotely. A
successful candidate establishes its recorded test scope; a console result does
not establish desktop behavior, and neither profile establishes physical wireless,
audio, an installer, release signing or channel promotion. A separate authorized
release service still owns production keys and publication.

## Current desktop source compatibility

The 2026-10-08 local WSL2 build completed all 72 runtime recipes, passed 97 tests
and booted the systemd image in a GTK window through WSLg. Its exact-image result
is `out/verification/systemd-wsl-rng/result.json`; the build summary is
`out/verification/build-result.json`. These ignored files are local evidence.

At the current pins, Telorgon `880abac6ae0a3e79856dba0001926c01e089ba65` lacks
the `desktop-settings-linux` feature and `services::desktop_settings` API required
by Shell `87a2744533c0703ed54eb63f5637b0021aaa0784` and Settings
`ce3b721563a7e071dcfaf512c981e2cbb5371c52`. `apps check` verifies SDK inputs;
`ready_to_build: true` does not establish Rust feature or API compatibility.
The app build stops at Cargo feature resolution. Matching source revisions or
reviewed upstream compatibility changes are required before composing a new
`desktop-use` image. Remote CI and desktop rendering remain unverified for
this source combination.
