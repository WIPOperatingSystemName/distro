# Requirements for supported releases

The current build is an experimental source-built distro. Maintain explicit
runtime evidence for each release rather than inferring readiness from a boot
marker or package archive test.

The local development build has passing exact-image receipts for persistent
boot, source-built systemd/PAM/logind services, Telorgon software-rendered desktop
windows and emulated keyboard/presentation/redraw, actual PAM password checks,
and local/strict-signed package upgrades. See the [README evidence table](../README.md).
The CI definitions exist but have not run remotely. None of these development
gates establishes installer/recovery, interactive account enrollment, GUI polkit
authentication, ScreenCast, physical hardware, public repository delivery or a
security maintenance service.

| Requirement | Gate |
| --- | --- |
| Boot and persistent root | Telorgon/UEFI QEMU boot using the composed disk and its exact digest |
| Desktop | Default Telorgon session; shell, File Explorer, Settings and picker; input, windows and packaged launchers |
| Sessions and privilege | Service manager, device/session management, PAM login, ordinary user accounts and polkit policy |
| Runtime services | D-Bus, PipeWire/session policy, networking, power management, certificates and portal brokers |
| Installation | Disk selection, partitioning, account creation and installation in a disposable VM; no host disk shortcuts |
| Recovery | Independently bootable recovery path, failed boot/upgrade tests and documented recovery procedure |
| Updates | Source-built download/signature stack; trusted keys, signed packages and repository metadata; atomic candidate promotion |
| Upgrade lifecycle | Previous supported image → real full upgrade → reboot; retained data/configuration and working desktop/services |
| Security maintenance | Track vulnerabilities across libc, parsers, crypto, network services, package tools, privileged services and applications as well as the kernel |
| Supply chain | Verified source identities, dependency/recipe/toolchain manifests, isolated builders, retained logs and auditable provenance |
| Reproducibility | Independent clean rebuilds and explained differences; deterministic packaging alone is insufficient |
| Licensing | Corresponding sources, notices, asset licenses and required static-library relinking materials |
| Hardware support | Separate physical UEFI, input, storage, GPU and network qualification; QEMU is one target |

The selected source versions are pinned development inputs. Pinning prevents
silent input changes but does not establish that a version is currently safe or
supported. Before a public release, audit the selected versions against upstream
advisories, choose a supported maintenance policy, and rebuild the affected
closure. Maintain an inventory/SBOM with exact versions and dependencies.

Build/package workers produce unsigned candidates. A separate release process
verifies approved source/build identities and qualification results, then signs
immutable packages, repository metadata and the manifest. Signing keys belong in
the release service, never in a PR worker, AI prompt, recipe, disk image or log.
Promote testing/stable channel pointers only after upgrade and reboot gates pass.

The private native assembly toolkit deliberately supports unsigned local file
repositories. Target pacman now includes source-built curl/GPGME with strict
signature defaults, and the signed local guest upgrade check has passed; see
[Authenticated updates](authenticated-updates.md). Public network updates still
require maintained release trust and lifecycle qualification. HTTPS transport alone does not
replace package/repository signature verification. Rollback protection, key
rotation, revocation and stale/replayed metadata policy also require tests.
