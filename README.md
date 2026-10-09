# Custom Distro

An independent Linux distribution built from source, with the Telorgon desktop,
application SDK and apps. Python coordinates the toolchain, packages, disk images
and QEMU testing; source-built pacman/libalpm manages the target packages.

## Build and run

After completing the [host setup](docs/build.md#host-setup), run from this directory:

```sh
python3 build.py run --profile systemd
```

This builds the toolchain, runtime packages and Telorgon EFI loader, composes a
systemd test image, then opens it in QEMU. Add `--headless` for a bounded boot
check. The first build needs network access, time and substantial disk space;
later runs validate and reuse cached stages. The default is four compile jobs.

For the full Telorgon desktop pipeline with compatible framework/app sources:

```sh
python3 build.py run
```

See the [build guide](docs/build.md) for prerequisites, offline builds, individual
stages and desktop checks. All generated files stay under `out/`.

## Development

```sh
python3 build.py validate
python3 -m unittest discover -s tests -v
```

- [Contributing](docs/contributing.md): recipes, component changes and pull requests.
- [Source modules](docs/sources.md): immutable Telorgon submodule pins.
- [Bootstrap](docs/bootstrap.md): declared host seeds and source-built toolchain.
- [Packages](docs/packages.md): ALPM format, dependencies and transactions.
- [Boot media](docs/boot.md): Telorgon EFI, images and private QEMU disks.
- [Release requirements](docs/release.md): installation, recovery and qualification.

This is an experimental development system. The normal desktop VM starts a local
`custom` session automatically; other systemd accounts remain locked. Development
package repositories are unsigned. Build and VM reports identify the actual
artifacts used; a desktop session alone does not qualify a release.

Coordination code is MIT licensed. Upstream components retain their own licenses.
