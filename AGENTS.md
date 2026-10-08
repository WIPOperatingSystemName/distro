# Custom Distro contribution rules

This project builds an independent Linux system from source. Keep application
source in the pinned Telorgon submodules under sources/; never mutate checkouts as build
setup. The default product uses the Telorgon bootloader and applications.

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
