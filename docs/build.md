# Build and run

Complete [environment setup](setup.md) first. Run commands from the distro root
in a Linux terminal. `./run` forwards arguments to `python3 build.py run`.

## Host setup

Use the [setup guide](setup.md) for host seeds, Rust, QEMU/OVMF, KVM and WSLg.
On Windows, apply the [WSLg frame-rate setting](setup.md#wslg-frame-rate) before
high-refresh testing. Run the pipeline as your normal user.

## Build and run

For source development, create a named development VM:

```sh
./run --profile desktop-dev --name dev --jobs 4
```

The pipeline audits the host, validates recipes, downloads verified sources,
builds/checks the bootstrap compiler, builds the private package toolkit and
runtime packages, builds Telorgon EFI and applications, composes the image and
opens QEMU. The first build needs network access and can take substantial time.
Failures stop at the affected stage and report its log.

Telorgon starts the local `custom` desktop session. Open applications from the
launcher and test mouse/keyboard behavior. The development profile adds the
source-built QEMU Guest Agent and private deployment channel.

## Desktop build

For a normal desktop without that channel:

```sh
./run
```

This defaults to `desktop-use`. Framework, loader and apps must form a compatible
source set. Builds include existing uncommitted source edits and never pull,
reset or advance submodules. A missing Rust feature/API requires a source fix
or an explicitly reviewed compatible source set; removing a requested feature
name does not supply its implementation.

Without `--name`, `run` chooses `test-<build-identity-prefix>`: the same image
build reuses its disk, while a different build gets a separate VM. This identity
is derived from build inputs, independently of filesystem timestamps in image
bytes. Use an explicit name when you want to retain one disk across builds.

## Reopen a saved VM

Reopen the development VM without compiling:

```sh
python3 build.py vm --use --development --name dev
```

For a normal desktop, use `python3 build.py vm --use --name my-desktop`.
`vm --use` defaults to the name `custom`; it does not automatically find the
VM selected by an earlier unnamed `run`. Reuse the exact printed name.

On first launch, `vm` needs an existing image. Its default base is
`out/images/custom-distro-desktop-dev.img` with `--development`, or
`out/images/custom-distro-desktop-use.img` otherwise. It does not build a missing
image or switch profiles. Use `run` for initial preparation.

Files, guest-installed packages and firmware variables live under
`out/vms/<name>/`. **An existing name retains its original disk even after the
base image is rebuilt.** Application deployment updates that disk explicitly.
Kernel, service-policy, runtime-library or loader changes need a newly composed
image and a new VM name for testing; application deployment cannot update them.
Do not delete a saved VM as a rebuild shortcut.

QEMU uses private virtual disks, copied firmware variables and user-mode
networking. It probes KVM and falls back to TCG. Normal desktop use has no boot
assertions or injected test input. Close the guest normally to retain its data;
closing QEMU or Ctrl+C also ends the launcher. Logs and the exact QEMU command
are saved alongside the VM state.

## Deploy applications into a running development VM

Implement fixes in the owning checkout under `sources/<module>/`. Generated
snapshots and experiments under `out/` cannot be the only copy of a fix.
Keep the named development VM running. In a second terminal:

```sh
python3 build.py deploy telorgon-file-explorer --name dev
```

The default deployment build profile is `dev`. For renderer/performance work,
use optimized code and rebuild every affected app. For a framework change:

```sh
python3 build.py deploy telorgon-shell telorgon-file-explorer \
  telorgon-settings-app telorgon-portal-picker --name dev --build-profile release --jobs 4
```

The pipeline snapshots the edited source, uses the recorded SDK, builds packages
with content-specific `.dev<build-identity>` versions, transfers and verifies
SHA256, runs **one guest `pacman -U --noconfirm` transaction**, and checks all
installed versions. It restarts the complete desktop session so clients release
old executable mappings. **Save work first: this closes desktop apps.**

Reopen apps and test the affected behavior. Installation and compositor startup
are separate from GUI verification. Record the source identity, VM/base image,
package versions/hashes, screenshots and measurements under `out/verification/`.
`out/vms/dev/deployment.json` records the transaction and restart result.

Useful options:

| Option | Behavior |
| --- | --- |
| `--build-profile dev` / `release` | Select debug iteration or optimized performance testing |
| `--online` | Permit fetching locked Cargo dependencies; deployment is otherwise offline |
| `--no-restart` | Keep the session; manually close/reopen updated applications to use new code |
| `--source-root PATH` | Explicit compatible workspace override; use the same workspace throughout |
| `--timeout SECONDS` | Guest command deadline, independent of compilation duration |

Development packages and receipts do not replace the ordinary package pointers
used for image composition. A later `./run` builds normal packages from the
source checkout. Never copy binaries over package-owned guest files to test a
fix. Ordinary library ABI changes require their corresponding runtime packages
and a fresh base; `deploy` handles application packages only.

## Incremental builds

Repeated `run` commands validate content inventories and reuse completed
bootstrap/runtime, loader, SDK, application and image stages when their inputs
and outputs match. Receipts live in `out/state/run/`; interrupted stages are not
reusable. Source changes normally reuse the essentials and SDK. Persistent Cargo
workspaces retain unchanged files and compiler outputs for deployment builds.

```sh
./run --profile desktop-dev --name dev --offline
./run --profile systemd --headless --verify
```

`--offline` requires cached archives, Cargo dependencies and the pinned compiler.
`--verify` runs the full preparation and integrity paths; it does not discard
valid lower-level compiler caches. Use `--jobs 2` for less compile parallelism.
There is no fixed compilation-time guarantee. Do not edit caches or receipts to
force reuse; inspect the failing inputs/log and retry after fixing the cause.

## Base-system and desktop checks

For a systemd test window that stays open after startup checks, use
`./run --profile systemd`. For bounded checks that exit automatically:

```sh
./run --profile systemd --headless --output out/verification/systemd
./run --profile console --headless --output out/verification/console
```

These profiles use temporary disk snapshots. The default startup deadline is
600 seconds; `--timeout` accepts 1–3600. Accounts remain locked in the systemd
profile; it is a service/session test, rather than an interactive login.
See [testing](testing.md) for desktop, hover, password and upgrade procedures.

## Desktop qualification

The `desktop` image profile adds automated qualification units and a genuine
Wayland input/presentation probe. It is separate from normal `desktop-use` and
`desktop-dev` sessions. Follow the [desktop qualification procedure](testing.md#desktop-qualification)
after the normal desktop build inputs are ready.

## Audio and camera

Saved desktop launches support `--audio auto`, `pulse` or `none`; `auto` detects
WSLg/PulseAudio endpoints. To select a backend without rebuilding:

```sh
python3 build.py vm --use --development --name dev --audio pulse
python3 build.py vm --use --development --name dev --audio none
```

Choose one launch command at a time. Pulse mode requires a QEMU build with the
`pa` backend and an accessible `PULSE_SERVER`/WSLg socket. It exposes playback
and microphone input to the guest; the guest still needs working PipeWire/ALSA
policy. An initialized disk from an older image does not gain new media drivers
just by changing this flag.

`vm --use --usb-camera BUS:ADDRESS` optionally passes one Linux-attached USB
webcam to the VM. On WSL, first attach it to Linux using the
[Microsoft USB device instructions](https://learn.microsoft.com/en-us/windows/wsl/connect-usb).
Use its actual Linux bus/address and ensure read/write device access. The
launcher rejects a device without a USB video interface. Passing through a
camera does not establish portal consent, capture or screen-sharing behavior.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| QEMU window cannot open | Check GTK support and the WSLg display environment; see [setup](setup.md) |
| Low visible frame rate on WSL | Verify `.wslgconfig`, `weston.log`, Windows/guest display rates and KVM separately |
| TCG instead of KVM | Read the launcher's probe failure and check `/dev/kvm` access; do not run the builder as root |
| Rebuilt changes absent | Confirm the VM name and deployment receipt; a saved disk is not replaced by image composition |
| Missing guest-agent socket | Boot a `desktop-dev` image with `--use --development` and the matching name |
| Missing image | Use `run` to build it; `vm` only launches existing media |
| Cargo feature/API mismatch | Inspect actual framework/app sources and preserve local edits; review compatible changes together |
| Guest install fails | Inspect `deployment.json`; failed transactions/restarts do not imply rollback |
| Build fails | Read its reported log and fix the owning source/recipe, then retry |

For CLI details use `python3 build.py --help`, `run --help`, `deploy --help`,
`vm --help` or the individual stage help. Source snapshots, packages and logs
remain under `out/`; [architecture](architecture.md) maps their locations.
