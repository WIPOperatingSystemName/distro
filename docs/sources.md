# Telorgon source modules

The distro repository owns recipes, build coordination, profiles and system
policy. Framework, loader and application source comes from Git submodules.
Each gitlink records an exact commit, so moving an upstream branch or release
tag does not change a distro checkout's inputs.

| Repository | Submodule directory |
| --- | --- |
| [telorgon](https://github.com/WIPOperatingSystemName/telorgon) | `sources/telorgon` |
| [bootloader](https://github.com/WIPOperatingSystemName/bootloader) | `sources/telorgon-bootloader` |
| [file-explorer](https://github.com/WIPOperatingSystemName/file-explorer) | `sources/telorgon-file-explorer` |
| [portal-picker](https://github.com/WIPOperatingSystemName/portal-picker) | `sources/telorgon-portal-picker` |
| [settings](https://github.com/WIPOperatingSystemName/settings) | `sources/telorgon-settings-app` |
| [shell](https://github.com/WIPOperatingSystemName/shell) | `sources/test-shell` |

Directory names preserve the existing application manifests and portable source
bundle layout. The distro itself is the parent repository.

```sh
git submodule update --init --recursive
git submodule status
git ls-files --stage sources/
```

The initial pins use the organization repositories' `main` commits observed on
2026-10-08. At that point GitHub reported no published releases or tags in those
repositories. These are immutable source selections, without a claim of release
qualification. The index entries with mode `160000` are the authoritative pins.

`apps prepare`, `loader` and `source-bundle export` default to `sources/`. They
copy inputs into `out/`, preserve Cargo locks, record content hashes and Git
HEAD/dirty state, and apply distro overlays only in those snapshots. Initialize
the modules before using these commands. Use `--source-root /path/to/workspace`
for an explicit development workspace or verified imported source bundle.

To select a reviewed release, fetch and check out its tag or full commit in the
relevant submodule, then stage that directory in the parent repository:

```sh
git -C sources/telorgon-file-explorer fetch origin <reviewed-tag-or-commit>
git -C sources/telorgon-file-explorer checkout --detach FETCH_HEAD
git add sources/telorgon-file-explorer
python3 build.py validate
python3 build.py apps prepare
python3 -m unittest discover -s tests -v
```

Review the parent gitlink change along with any required package revision,
dependency or overlay changes, then qualify affected builds and runtime behavior.
Normal setup uses `git submodule update --init --recursive` to restore the parent
pins. Avoid `git submodule update --remote` during builds because it selects new
upstream branch heads instead of those pins.
