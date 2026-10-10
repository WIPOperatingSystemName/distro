# Source modules

The distro repository owns recipes, build coordination, image profiles and
system policy. Component source lives in these Git submodules:

| Owning repository | Directory |
| --- | --- |
| [telorgon](https://github.com/WIPOperatingSystemName/telorgon) | `sources/telorgon` |
| [bootloader](https://github.com/WIPOperatingSystemName/bootloader) | `sources/telorgon-bootloader` |
| [file-explorer](https://github.com/WIPOperatingSystemName/file-explorer) | `sources/telorgon-file-explorer` |
| [settings](https://github.com/WIPOperatingSystemName/settings) | `sources/telorgon-settings-app` |
| [portal-picker](https://github.com/WIPOperatingSystemName/portal-picker) | `sources/telorgon-portal-picker` |
| [shell](https://github.com/WIPOperatingSystemName/shell) | `sources/test-shell` |

Each parent gitlink records an exact component commit. Inspect the selections:

```sh
git submodule status
git ls-files --stage sources/
```

Initialize a new checkout with `git submodule update --init --recursive`.
On an existing workspace, inspect component work first; restoring the parent's
pins can move a component checkout. Never use `submodule update --remote`, pull,
reset or patch the component as incidental build setup.

## Edit source and snapshot it

Edit the owning checkout directly. The build pipeline's default source root is
`sources/`; its snapshots include existing uncommitted work, Cargo locks,
content hashes, executable modes and Git HEAD/dirty state. Distro-specific
recorded overlays are applied only in generated snapshots. They must not become
the home for application/framework fixes.

`apps prepare`, `loader` and `source-bundle export` snapshot the selected inputs
under `out/`, excluding Git internals, output trees and vendor caches. Generated
copies are not development source. A fix is complete when it lives in the owning
source and its rebuilt package passes the affected checks.

For a separate development workspace, use `--source-root /absolute/workspace`.
It must contain the framework, bootloader and app directories in the layout above.
Pass the same root to `run`, `deploy` and source export as appropriate; record the
origin and source identity in test evidence. Use the
[pacman deployment loop](build.md#deploy-applications-into-a-running-development-vm)
for application iteration.

## Portable source bundles

To export the current source checkouts, including local edits:

```sh
python3 build.py source-bundle export
```

The command reports a deterministic archive and SHA256 under `out/bundles/`.
On a clean worker, import that archive with its independently reviewed digest:

```sh
python3 build.py source-bundle import /path/to/telorgon-sources.tar.xz --sha256 REVIEWED_SHA256
```

Replace the path and digest with the export's actual values. Import verifies the
archive and records its extracted workspace in `out/bundles/current.json`.
Use that root explicitly:

```sh
CD_SOURCE_WORKSPACE=$(python3 -c 'import json; print(json.load(open("out/bundles/current.json"))["root"])')
./run --profile desktop-dev --name bundle-test --source-root "$CD_SOURCE_WORKSPACE"
```

Bundles carry source, assets, license inputs and locks; they exclude credentials,
Git internals and build outputs. An imported bundle is a replay input. Implement
subsequent fixes in the owning repository and export again.

## Reviewed pin updates

A component commit/PR and a distro gitlink update are separate review steps.
Pin updates require explicit authorization and a compatible reviewed component
set. Keep corresponding package revisions and dependency changes with the
integration, then rebuild and test affected behavior. Do not advance a detached
source checkout just because its upstream branch has moved.
