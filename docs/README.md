# Development documentation

Start with [environment setup](setup.md), then [build and run](build.md).
All commands run from the distro checkout in a Linux terminal unless a block
explicitly says Windows PowerShell. The `./run` wrapper calls the Python CLI.

| Guide | Use it for |
| --- | --- |
| [Environment setup](setup.md) | Ubuntu/Linux seeds, WSL2, WSLg frame rate, Rust and checkout setup |
| [Build and run](build.md) | First build, saved VMs, incremental rebuilds and pacman deployment |
| [Testing](testing.md) | Hover measurements, desktop input/presentation, boot, password and upgrade gates |
| [Contributing](contributing.md) | Source changes, recipes, CI and commit/PR handoff |
| [Source modules](sources.md) | Repository ownership, immutable pins and portable source bundles |
| [Architecture](architecture.md) | Bootstrap, boot images, session services and supported runtime capabilities |
| [Packages](packages.md) | Build dependencies, ALPM archives, trust and package provenance |
| [Release requirements](release.md) | Qualification and signing requirements for a supported release |

Use these guides for current procedures. Per-run logs, hashes, screenshots and
measurements belong under `out/`; they qualify only the artifacts they identify.
Do not turn an old successful receipt into a claim about a new build.
