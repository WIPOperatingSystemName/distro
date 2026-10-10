# Custom Distro

Custom Distro builds an independent Linux system from source, with the Telorgon
bootloader, desktop shell, framework, File Explorer, Settings and portal picker.
Python coordinates the toolchain, packages, images and private QEMU VMs. The
guest uses source-built pacman/libalpm; it does not import another distro's
runtime packages or root filesystem.

## Start here

Set up [Linux or WSL2/WSLg](docs/setup.md) first, including the
[WSLg frame-rate setting](docs/setup.md#wslg-frame-rate) for high-refresh testing.
Run from the checkout in a Linux terminal:

```sh
./run --profile desktop-dev --name dev --jobs 4
```

This builds the development image and opens a saved VM. Open File Explorer and
Settings from Telorgon's launcher. Close the guest normally when finished;
its disk and firmware variables remain under `out/vms/dev/`.

To reopen that VM without building:

```sh
python3 build.py vm --use --development --name dev
```

## Change source and test it

Edit the owning repository under `sources/`. Keep QEMU running, then use a second
Linux terminal to build and install the affected apps through guest pacman:

```sh
python3 build.py deploy telorgon-shell telorgon-file-explorer \
  telorgon-settings-app telorgon-portal-picker --name dev --build-profile release
```

Deployment restarts the desktop and closes apps, so save work first. Test the
actual interaction after installation. A framework renderer change requires
rebuilding all four apps. Generated snapshots under `out/` are build outputs;
the implementation belongs in the source checkout.

For a normal desktop image without the deployment channel, use `./run`.
For a bounded base-system boot check, use:

```sh
./run --profile systemd --headless
```

See [build and run](docs/build.md) for saved-disk behavior, cache reuse, profiles,
audio/camera controls and troubleshooting. Rebuilding an image does not upgrade
an existing named VM's disk; deploy apps or use a new VM name for a fresh base.

## Documentation

The [documentation index](docs/README.md) covers environment setup, daily
commands, testing, architecture, packages, source ownership and contributions.
Runtime reports belong under `out/verification/` and identify their actual
images and installed packages. This is a development system; installer/recovery,
public update delivery and physical hardware need the separate gates in
[release requirements](docs/release.md).
