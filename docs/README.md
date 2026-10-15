# Development documentation

## Start here

1. **[Environment setup](setup.md):** choose Linux or WSL2, install prerequisites,
   create the workspace and check that it is ready to build.
2. **[First build](build.md#first-build):** choose a desktop profile, build the OS
   and open your first saved VM.
3. **[Daily development](build.md#update-changed-applications-automatically):**
   update applications in that VM, then follow [testing](testing.md) to exercise
   your changes.

Setup steps identify the terminal and working directory to use. After setup,
build commands run from `~/wip-os/distro` in a Linux terminal. The `./run` wrapper
calls the Python CLI.

The build guide covers [automatic distro and component updates](build.md#automatic-source-updates),
including preservation of local work and offline builds.

## Guides

| Guide | Use it for |
| --- | --- |
| [Environment setup](setup.md) | Fresh Ubuntu/Linux/WSL2 setup, Universe, host tools, KVM group access and login activation, Rustup, Git identity, graphical sessions, workspace checks, WSLg frame rate and black-window troubleshooting |
| [Build and run](build.md) | Numbered first build, verified source downloads and fallbacks, saved VMs, default development login shell and password enrollment, user settings and file dialogs, progress and logs, build speed, cleanup, troubleshooting and application deployment |
| [Testing](testing.md) | System shell authentication, user applications, display, wallpaper and sound settings, logout isolation, browser and terminal checks, hover measurements, desktop input/presentation, boot, password and upgrade gates |
| [Contributing](contributing.md) | Source changes, recipes, CI and commit/PR handoff |
| [Source modules](sources.md) | Repository ownership, main tracking and portable source bundles |
| [Architecture](architecture.md) | Runtime layers and an individual explanation of every package, source module, bundled service, guest policy file and boot/assembly piece |
| [Component map](components.md) | Every declared package and source module, grouped by function in a layered SVG and linked index |
| [Packages](packages.md) | Build dependencies, Rust seeds, ALPM archives, trust and package provenance |
| [Release requirements](release.md) | Qualification and signing requirements for a supported release |

Use these guides for current procedures. Per-run logs, hashes, screenshots and
measurements belong under `out/`; they qualify only the artifacts they identify.
Do not turn an old successful receipt into a claim about a new build.
