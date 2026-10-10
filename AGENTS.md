# Custom Distro contribution rules

This project builds an independent Linux system from source. Keep application
source in the Telorgon submodules under sources/. Implement component changes in
their owning source repository, including existing uncommitted work. Never pull,
reset, patch, or advance those checkouts as incidental build setup. The default
product uses the Telorgon bootloader and applications.

## Source changes and VM testing

- Fix application and framework code under `sources/<module>/`. `out/` holds
  generated snapshots, caches, packages and evidence; edits there are not a
  completed implementation. Never hand-edit a generated source snapshot or
  leave a successful experiment as the only copy of a fix.
- Use the Python pipeline to snapshot the edited source and build packages.
  The default source root is `sources/`; use `--source-root` only for an
  explicitly selected source workspace, and record it in the test evidence.
- Test requested desktop behavior in a private `desktop-dev` VM. Build and
  install through `python3 build.py deploy <packages> --name <vm>`; it verifies
  transfers, runs one guest `pacman -U` transaction and checks installed versions.
  Never test by copying executables or libraries over package-owned guest files.
- Framework changes require rebuilding and deploying every affected application.
  Use `--build-profile release` for rendering/performance tests. Save work before
  a session restart; prefer a separate named test VM over disturbing a user's VM.
- Exercise the affected behavior after installation. Record source identity,
  package versions/hashes, VM identity and actual observations under `out/`.
  Compilation, pacman success and compositor startup are separate from GUI
  qualification. If VM testing is blocked, report the blocker and leave the fix
  in the owning source repository; do not substitute experimental build sources.
- See [the build/deploy guide](docs/build.md#deploy-applications-into-a-running-development-vm).

## Documentation

- Keep [the documentation index](docs/README.md) current. Environment setup,
  including WSL2 and the WSLg frame-rate setting, belongs in [setup](docs/setup.md).
  Daily build, saved-VM and pacman deployment commands belong in [build](docs/build.md).
- Verify documented commands against the Python CLI and actual launcher behavior.
  Distinguish rebuilding an image from upgrading an existing named VM's disk.
- Remove superseded guides and fix their incoming links instead of retaining
  competing setup paths or historical status pages. Keep reusable test procedures
  in [testing](docs/testing.md); exact run hashes, timings and logs belong under
  `out/`, with their actual qualification scope.
- When toolchain, profiles, deployment or VM options change, update the relevant
  guide and README together. Keep source pins and package versions authoritative
  in their manifests rather than copying dated inventories into prose.

Use the Python CLI for local work and CI. Package changes belong in one recipe
directory with immutable source pins, runtime dependencies, and package revisions.
Do not overwrite published package bytes under an existing version. Validate
changed recipes and run checks that exercise the affected behavior.

Host compilers and development bootstrap artifacts are declared seed inputs.
They are not final OS packages. Do not borrow another distro's root or runtime
binary packages. Every image report must identify the artifacts it actually uses.

Keep outputs under out/. Do not install host packages, modify host firmware,
mount physical disks, or operate services as incidental setup. QEMU uses private
virtual disks and firmware variables. Trusted development builds may use the
host seed environment; untrusted PR recipes require disposable isolated workers.

Never put signing credentials in recipes, images, logs, or prompts. Record actual
boot/install/upgrade results; compilation alone does not qualify runtime behavior.
Use plain project and module names such as custom-distro and distro_build.

## Commit and pull request handoff

Read the [shared organization contribution instructions](https://github.com/WIPOperatingSystemName/.github/blob/main/AGENTS.md)
before the final handoff. In the standard contributor workspace, the local
copy is `~/wip-os/.github/AGENTS.md`. If neither copy is available, follow this
minimum workflow:

- After completing requested edits and relevant verification, summarize the
  changes and suggest a commit message. Ask once whether the user wants a
  commit and PR to `WIPOperatingSystemName/distro`'s default branch.
- Honor explicit submission authorization or a prior decline without asking
  again. Otherwise wait for approval before committing, pushing or opening
  a PR; leave the changes local if the user declines.
- Before starting or resuming work, verify the canonical organization remote and
  fetch it and the personal fork. Integrate the organization's latest default
  branch into the contribution checkout, preserving local work and history.
  In the standard setup, use `git pull --ff-only origin main` on a clean
  `main`; if branches diverge, inspect and merge instead of resetting or
  force-pushing. Do not advance distro's pinned submodules as build setup.
- Fetch the organization again immediately before submission and require
  `git merge-base --is-ancestor origin/main HEAD` to pass, adapting remote and
  branch names to the verified setup. Integrate missing upstream changes and
  rerun affected checks. Report the verified upstream commit or any sync blocker.
- Submit only intended files, normally from the personal fork's existing
  `main` for one active contribution per repository; do not create a new branch
  for every PR. Preserve the source branch of an existing PR and update it for
  the same work. Push to the personal fork and target the organization's default
  branch. After a merge, synchronize from the organization before the next task.
  Keep component changes in their owning repository. Do not merge the PR
  or update distro submodule pins without separate authorization.
