# Contributing

Set up the [development environment](setup.md), then use the Python
[build and deployment loop](build.md). Read the root [AGENTS.md](../AGENTS.md)
and the owning component's instructions before changing code.

## Source ownership

Implement framework, loader and application changes in their owning repository
under `sources/`. The distro owns recipes, profiles, system policy and build/VM
coordination. [Source modules](sources.md) maps those owners.

Generated snapshots under `out/` are immutable build inputs, rather than an
implementation workspace. Move a successful experiment into the actual source
before rebuilding or declaring a fix complete. The pipeline includes existing
uncommitted edits without changing submodule pins. Use `--source-root` only for
an explicitly selected compatible workspace, and record that origin.

For application work, install the rebuilt packages through `build.py deploy` in
a private named development VM. Rebuild every affected consumer of a framework
change; use `--build-profile release` for performance checks. Test the actual
behavior after pacman installation and retain the source/package/VM identities.
Kernel, loader, policy and library changes require a fresh image and VM name.
See [testing](testing.md) for the appropriate evidence.

## Recipe changes

Keep a package change in `packages/<name>/`. Record immutable upstream URLs and
verified SHA256, license notices, runtime dependencies, preserved configuration
paths and package revision. The build stages into `CD_STAGE_DIR`; it never
installs into the host's `/usr` or starts host services.

```sh
python3 build.py validate
python3 build.py plan pacman
python3 build.py affected glibc
```

Substitute the affected package names for your work. `native` and `target`
dependencies determine compile ordering and reject cycles; `runtime` dependencies
describe the installed closure and can contain cycles. Compile sysroots retain
conflict/ownership/digest checks while omitting runtime-only dependency checks.
Images and ordinary guest installs check the full closure. Keep host generator
artifacts distinct from target packages, even on the same CPU architecture.

Increment the version/revision for changed published payloads or metadata,
including ABI rebuilds. Never overwrite different package bytes under an
existing name/version/architecture. Use appropriate recipe, archive and real
runtime checks; compilation alone does not qualify a service or desktop change.
[Packages](packages.md) describes the format and provenance contract.

## Local checks and CI

```sh
python3 -m unittest discover -s tests -v
python3 build.py validate
```

Run focused checks during iteration and the required affected checks before
handoff. Some integration tests require built artifacts or private Unix sockets;
report skips and environment blockers rather than claiming coverage.

The workflow definitions under [`.github/workflows`](../.github/workflows)
include catalog/tooling checks and manually dispatched disk candidates. The
candidate workflow supports `console` and `desktop`, with pinned actions and
read-only repository permissions. Its desktop job requires a fresh disposable
x86_64 Linux worker labelled `custom-distro-ephemeral`, at least 128 GiB free disk
and 16 GiB RAM. The selected compile-job count must fit the worker.

Untrusted recipes are executable code. Run them only on disposable isolated
workers without signing keys, deployment credentials or persistent privileged
host access. The trusted local developer executor is not an isolation boundary.
Destroy fixture-bearing workers after qualification. Keep password fixtures,
private Cargo state and TLS/signing test keys out of published artifacts; retain
public receipts and exact source/build identities instead. A workflow definition
is not proof that a remote run passed.

## Portable sources

`source-bundle export/import` carries exact framework, loader and application
inputs to a clean worker. See [source bundles](sources.md#portable-source-bundles).
Candidate CI defaults to the checked-out pins; an override requires both an HTTPS
bundle URL and a reviewed SHA256. Replaying local edits requires exporting those
edits and explicitly selecting the imported workspace in the build commands.

## Commit and PR handoff

Follow the [shared organization instructions](https://github.com/WIPOperatingSystemName/.github/blob/main/AGENTS.md).
Verify organization/fork remotes and fetch both before choosing a baseline.
Integrate the organization's latest default branch without resetting, silently
stashing or discarding work. Use a clean separate contribution checkout when
pending edits prevent safe integration. Never advance distro's source pins as
incidental setup.

After implementation and checks, inspect the intended diff, summarize behavior
and evidence, and suggest a commit message. Ask once for commit/push/PR approval
unless already authorized or declined. Submit a separate commit and PR for each
owning repository; a component source change belongs to that component. Reuse
the existing contribution branch for the same work, normally the fork's `main`.

Immediately before submission, fetch upstream again and require its default
branch to be an ancestor of the submitted HEAD. Integrate missing changes and
rerun affected checks. Record that verified commit or the concrete sync blocker.
Commit/PR approval does not authorize merging, publishing a release or advancing
distro submodule pins. Maintainers review and merge PRs manually; ordinary source
and catalog/tooling checks remain in place. Component merges do not update distro
pins. Propose those updates separately and record the relevant build and VM
results. See the [maintainer guide](https://github.com/WIPOperatingSystemName/.github/tree/main/automation).
