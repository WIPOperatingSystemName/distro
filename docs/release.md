# Requirements for supported releases

This is a development distro. Qualify each candidate's actual source, packages,
image and installed state; an older receipt, successful build or boot marker
cannot establish a new release's behavior. [Testing](testing.md) describes the
implemented local gates and their evidence limits.

| Requirement | Required evidence |
| --- | --- |
| Boot and persistent root | Exact Telorgon/UEFI disk boot with packaged OS identity |
| Desktop | Shell and actual app windows, input/presentation, portals and relevant interactions |
| Sessions and privilege | systemd/device management, PAM, user sessions, real login and polkit behavior |
| Runtime services | D-Bus, media/session policy, networking, power, certificates and portal brokers |
| Installation | Disk selection, partitioning, account creation and installation in a disposable VM |
| Recovery | Independently bootable rescue path and failed boot/update recovery tests |
| Updates | Source-built download/signature stack, maintained trust, signed packages/databases/manifests |
| Upgrade lifecycle | Previous supported image → full upgrade → reboot, preserving data/configuration and services |
| Security maintenance | Vulnerability tracking and response across the complete runtime/application closure |
| Supply chain | Verified pins, isolated builders, exact toolchain/dependency manifests and auditable provenance |
| Reproducibility | Independent clean rebuilds and explained differences |
| Licensing | Corresponding sources, notices, asset licenses and required relinking materials |
| Hardware | Separate physical UEFI, storage, input, GPU, audio and network qualification |

Source pinning prevents silent input changes; it does not establish maintenance
or security support. Recipes are the version inventory. Audit selected versions
against upstream advisories, define a supported maintenance policy and retain
an SBOM with exact dependency/build identities before publication.

Build workers produce unsigned candidates. A separate authorized release
service verifies approved source/build identities and actual qualification,
then signs immutable packages, repository metadata and the release manifest.
Keep signing keys out of PR workers, local fixtures, recipes, images and logs.
Promote channel pointers only after the release and upgrade/reboot gates pass.
HTTPS transport does not replace package/database signature verification.
Key rotation/revocation, stale or replayed metadata and offline recovery trust
also need explicit policy and tests.

Private development images record their local unsigned trust policy; signed
fixture images carry ephemeral public verification material. Neither is a
production trust bootstrap. Normal desktop autologin is a private VM policy,
rather than an implemented installer or account-enrollment workflow.

Remote CI runs, public delivery, screen sharing, physical hardware and recovery
must be supported by their own current evidence. Inspect the actual package
capture-capability record rather than assuming that an installed picker or a
CPU/Vulkan backend establishes sharing support. A passed host-kernel API probe,
pacman transaction or QEMU desktop test cannot qualify all of these gates.
