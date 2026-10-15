# Build and run

Complete [environment setup](setup.md) first. All commands below run from the
**distro directory in a Linux terminal**, as your normal user. The `./run`
launcher forwards arguments to `python3 build.py run`.
Both entry points update distro from its canonical upstream before building;
see [automatic source updates](#automatic-source-updates).

## First build

These steps create a desktop development image and a saved VM named `dev`.
That profile includes the source-built QEMU Guest Agent and private application
deployment channel. For a desktop without that channel, use the
[normal desktop profile](#desktop-build) instead.

### 1. Enter the checkout and validate it

```sh
cd ~/wip-os/distro
python3 build.py validate
```

Continue when validation finishes without an error. If you have not yet run the
host checks, complete [environment verification](setup.md#4-verify-the-environment)
before starting the build.

### 2. Build the OS and open QEMU

```sh
./run --profile desktop-dev --name dev
```

No separate bootstrap or manual package-build sequence is needed. The first
build needs internet access and can take substantial time. The pipeline:

1. Fetches distro's canonical default branch and fast-forwards the current
   checkout when safe, restarting Python if the coordinator changes.
2. Updates component clones to organization `main`, audits host tools, validates
   recipes and downloads verified sources.
3. Builds and checks the bootstrap compiler and private package tools.
4. Builds the OS runtime packages, Telorgon EFI, desktop SDK and applications.
5. Composes the OS image, prepares a private VM disk and opens QEMU.

The build chooses a CPU/memory-aware job count up to eight. Let it choose the
default, or add `--jobs 4` to set a smaller budget. See
[build speed and parallelism](#build-speed-and-parallelism) for the controls.

Watch the progress bar for the current stage and elapsed time. Large compiler
stages can take a long time at one percentage; see [build progress](#build-progress).
If a stage fails, read its reported log, fix the underlying problem and rerun the
same command. Completed work is reused; cleaning is unnecessary for a retry.
Source downloads try the primary URL, then any `fallback_urls` declared in the
source manifest if a download fails or times out. Each URL must use HTTPS and
the downloaded bytes must match the same SHA256 pin. A checksum mismatch stops
the build. GCC's bootstrap source declares an alternate official server.
See [troubleshooting](#troubleshooting) for common failures.

### 3. Enroll a testing password and sign in

Desktop-dev boots the system-owned login shell with one `custom` account. In a
second **Linux terminal, distro directory**, enroll its password on the running
private VM:

```sh
python3 build.py dev-password --name dev
```

Enter and confirm the password at the hidden prompts. The command uses the
private development channel and PAM; the password stays out of images, command
arguments and reports. It changes only this saved VM's `custom` account. Select
`custom` in the QEMU login view and enter that password. The same renderer then
loads the user's desktop. The account and preferences persist on the VM disk.

Open File Explorer and Settings from the desktop panel and check mouse and keyboard
input. A completed progress bar establishes image preparation and QEMU startup;
it does not establish that the guest desktop or applications work correctly.

The VM's private disk, firmware variables and logs live under `out/vms/dev/`.
Close the guest normally when finished. To check a newly rebuilt base later,
choose a **new VM name**: rebuilding the image does not replace a saved disk.

### 4. Reopen the VM or begin development

Reopen the saved development VM without building:

```sh
python3 build.py vm --use --development --name dev
```

When you are ready to edit source, keep this VM running and follow
[update changed applications](#update-changed-applications-automatically) in a
second Linux terminal. Implement changes under `sources/`, then build and install
packages through guest pacman. The [testing guide](testing.md) describes how to
exercise the installed behavior.

## Build progress

`./run` and `python3 build.py run` show progress automatically, including on a
first build with no cached outputs. The display covers component updates,
host/recipe checks, downloads, the bootstrap toolchain and its verification, package tools, OS
packages, Telorgon EFI, the desktop SDK and applications, OS image assembly,
and private VM preparation/startup. Console/systemd builds omit desktop steps.

The bar counts completed steps. Package/application counts and bootstrap stage
names show progress inside larger steps. Parallel packages list active builds
and advance their count as each finishes, including cached packages.
Percentages describe completed work
units, rather than estimated time. Compiling GCC can hold the overall percentage
for a long time. The activity indicator and elapsed time keep updating while
commands run or cached inputs are checked. Verified cached steps are marked as
reused. Failures and interruptions stop the bar at the affected step.

In a terminal the display updates in place on stderr. Redirected output uses
plain progress lines with a heartbeat every 30 seconds. Structured events,
command log paths and progress details are saved to the printed
`out/logs/run-<profile>-<timestamp>.jsonl` file. Existing compiler and VM logs
remain in their usual locations. Use `./run --no-progress` to retain the previous
JSON event output without a progress display or additional progress log.

For normal desktops, 100% means the OS image and private VM preparation are
complete and QEMU has started; guest boot and GUI behavior still require actual
qualification. The bar stops before waiting for you to close QEMU. Console and
systemd runs finish the bar when their expected boot markers are observed.

## Desktop build

For a normal desktop without that channel:

```sh
./run
```

Open **Firefox** from the application launcher to browse the web. HTTP, HTTPS and HTML links use Firefox
by default.

The desktop includes the Foot terminal. Open **Foot** from the application launcher
or press **Ctrl+Alt+T** with desktop focus. A terminal window with a shell prompt confirms
that it launched. Fonts and terminal capability data are included in the image.

This defaults to `desktop-use`. Framework, loader and apps must form a compatible
source set. A missing Rust feature/API still requires compatible implementations
in the source set. See [automatic source updates](#automatic-source-updates) and
[component source synchronization](sources.md) for details.

Without `--name`, `run` chooses `test-<build-identity-prefix>`: the same image
build reuses its disk, while a different build gets a separate VM. This identity
is derived from build inputs, independently of filesystem timestamps in image
bytes. Use an explicit name when you want to retain one disk across builds.

### Automatic source updates

Online `./run` and `python3 build.py run` first verify distro's canonical
organization origin, fetch its advertised default branch and fast-forward the
current checkout. If distro changes, the entry point restarts Python before
loading the CLI and build catalogs. New default applications, recipes, profiles
and registered components are therefore available to that build. Component
clones under `sources/` then follow organization `main` before snapshotting.

Local branches, contribution commits and uncommitted work are preserved when
Git can fast-forward safely. Overlapping edits, diverged history and network
errors stop the run before compilation; resolve the reported issue in the owning
repository and rerun. Nothing is reset or stashed.

Use `./run --offline` to keep both distro and component checkouts unchanged;
required archives and Cargo dependencies must already be cached. Help commands
also skip updates. Updating the checkout and rebuilding an image does not
upgrade an existing named VM's disk: use a new VM name to test a changed base.

## Independent system shell

This mode is the default for `desktop-dev`; `desktop-use` selects it with
`--system-shell`. It runs the same shell executable as a system service under the
dedicated `telorgon-shell` account. Its compositor owns a private runtime
directory and starts without a personal desktop, settings, applications or user
bus. Boot explicitly requests a login view through the root-only system bus
control interface. The view offers the local accounts listed in
`/etc/telorgon/login-users`, initially `custom`. A separate root session manager
authenticates through PAM, opens a logind session and starts an unprivileged
session process with the user's own bus and runtime directory. The same renderer
then shows a desktop with application buttons and a sign-out action. The user
session discovers XDG desktop entries and launches applications with the user's
home, permissions and bus. It supplies a private Wayland socket to the renderer,
which admits only that user's clients and disconnects them on logout. The user's
Settings service controls display previews through a private connection tied to
that login. Confirmed display preferences stay in the user's home and are restored
at the next login; logout resets the display for the login screen. Wallpaper is
validated and decoded by the user helper, then supplied as immutable pixels to
the renderer. The user's background is restored at login and cleared on logout;
the renderer does not read the user's home. The user helper observes sound and
restores saved device, volume and mute preferences. When it starts PipeWire,
WirePlumber and Pulse compatibility, those processes belong to the user's logind
scope and end with that session. An existing user audio server is reused. Audio
device discovery and reconnects do not require restarting the system renderer.
The helper also supervises the desktop portal, document portal, permission store
and Telorgon FileChooser backend on the user's bus. Settings uses that portal
for Add Photo; dialogs inherit the user's display and end with the login session.
System shell mode does not yet expose a ScreenCast backend.
Default accounts remain locked;
password enrollment for testing belongs only in the disposable guest.

1. **Linux terminal, distro directory:** build a fresh development image and
   choose an unused VM name:

   ```sh
   ./run --profile desktop-dev --name system-shell-test
   ```

   Use `--offline` to replay edited local component sources. This mode selects
   the profile's system shell package set; it omits the normal desktop's browser
   and terminal. The generated image alias is
   `out/images/custom-distro-desktop-dev.img`. To explicitly build the previous
   automatic user desktop, add `--user-shell`; its separate alias is
   `out/images/custom-distro-desktop-dev-user-shell.img`.

2. **Second Linux terminal, distro directory:** enroll the testing password:

   ```sh
   python3 build.py dev-password --name system-shell-test
   ```

   Confirm the login view renders and select the `custom` account, then follow the
   [authentication and isolation checks](testing.md#system-shell-isolation).
   Locked accounts and incorrect passwords must stay on the login screen.

3. Reopen that saved disk without rebuilding:

   ```sh
   python3 build.py vm --use --development --name system-shell-test \
     --image out/images/custom-distro-desktop-dev.img
   ```

   Deploy shell application changes using the normal `deploy` command. It reads
   the guest manifest and restarts the system shell service for this mode.
   Service/account policy changes require a new image and unused VM name.

   When editing the framework, rebuild and deploy every affected application:

   ```sh
   python3 build.py deploy telorgon-shell telorgon-settings-app \
     telorgon-file-explorer telorgon-portal-picker --name system-shell-test \
     --build-profile release --offline
   ```

   Complete the [application and logout checks](testing.md#system-shell-isolation)
   after installation; a successful package transaction alone does not qualify
   the desktop.

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

## Update changed applications automatically

Build a `desktop-dev` image once and keep its named VM running. Inspect the
first update in a second terminal:

```sh
python3 build.py dev --name dev --plan --adopt-base
python3 build.py dev --name dev --adopt-base --jobs 4
```

First adoption compares the recorded SDK and native package inventory with the
guest and records the current base inputs after a successful update. Confirm
that the VM was built from your intended base before adopting: this check cannot
reconstruct historical source edits used by an older image. `--plan` snapshots
source and writes a report under `out/`, but does not fetch, compile, install or
adopt the baseline. Review any reported base errors before continuing.

For subsequent edits:

```sh
python3 build.py dev --name dev --jobs 4
python3 build.py dev --name dev --build-profile release
```

The command discovers `packages/*/application.toml` and selects the application's
runtime dependency closure from the guest's image profile. It uses each app's
declared source projects, recipe, toolchain and SDK to calculate the same build
identity as the package builder. Already installed identities are skipped;
valid cached packages are reused. A shared framework change selects every app
that declares it. All selected packages use one verified guest pacman transaction.
No image composition or VM disk replacement occurs.

Before deployment, `dev` verifies canonical component origins and fast-forwards
the local clones to organization `main`, preserving local work. Conflicting edits
or diverged history stop for deliberate integration in the owning repository.
It also fetches distro's default branch and stops if the build coordinator itself
needs updating; it never updates its own running code. `--plan` remains read-only.

`--offline` uses current local source and cached Cargo inputs without fetching or
advancing checkouts, even when a cached upstream reference is newer. Explicit
portable source bundles replay their recorded contents instead of following Git.
The same component synchronization applies to `run`, `loader`, `apps prepare/build`,
`deploy` and source-bundle export. Use `python3 build.py sources sync` to update
components without building anything.

Native recipe/runtime, SDK, loader, system-policy and base-profile changes stop
the app loop. Build a fresh image with `run` and use a new VM name, then inspect
and adopt that VM. `--adopt-base` cannot overwrite an existing baseline to bypass
this check. Adding an app that needs only existing native packages does not
require a new base; adding a new native dependency does.

Restart behavior comes from each app manifest: ordinary apps are listed for
manual reopening, services use declared user units, and shell changes restart
the desktop. **Save work before shell updates: a desktop restart closes apps.**
`--no-restart` defers all restarts. Installation/version checks do not qualify
GUI behavior; exercise the affected apps afterwards. The latest attempt is
recorded in `out/vms/<name>/development-report.json`; the last successful base
and package identities live in `development.json`. Failed attempts preserve the
last successful baseline. Rebuilding an image does not upgrade the saved disk.

Future apps join this workflow through the
[source and application catalogs](sources.md#register-another-application).

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

If an image build is running in another terminal, completed regular application
packages can be deployed without waiting for its build lock:

1. **Linux terminal, distro directory:** finish the selected application builds
   against the recorded SDK. Include every affected app for a framework change:

   ```sh
   distro_sdk=$(python3 -c 'import json; print(json.load(open("out/sdk/current.json"))["sysroot"])')
   python3 build.py apps build telorgon-shell telorgon-file-explorer \
     telorgon-settings-app telorgon-portal-picker --sysroot "$distro_sdk" --offline
   ```

2. **Linux terminal, distro directory:** install those completed packages:

   ```sh
   python3 build.py deploy telorgon-shell telorgon-file-explorer \
     telorgon-settings-app telorgon-portal-picker --name dev \
     --build-profile release --offline --reuse-built
   ```

   Reuse verifies that current source contents, recipes, SDK and build profile
   match the completed packages. A mismatch stops before installation. This
   mode keeps regular package versions and still verifies transfers, runs one
   transaction and checks installed versions. Check `deployment.json` for
   success, then exercise the actual application behavior.

Useful options:

| Option | Behavior |
| --- | --- |
| `--build-profile dev` / `release` | Select debug iteration or optimized performance testing |
| `--online` | Fetch component main branches and locked Cargo dependencies (the default) |
| `--offline` | Keep current local source and use cached Cargo dependencies without fetching |
| `--no-restart` | Keep the session; manually close/reopen updated applications to use new code |
| `--reuse-built` | With `--offline`, verify and install matching completed regular packages without compiling or acquiring the image build lock |
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

## Build speed and parallelism

Build commands choose a CPU/memory-aware job count by default, capped at eight.
The policy respects Linux CPU affinity and cgroup v2 CPU/memory limits and budgets
roughly 1.5 GiB per compiler job with 512 MiB left for coordination. Override it
explicitly for your machine:

```sh
./run --profile desktop-dev --name dev --jobs 8 --package-workers 2
python3 build.py build busybox zlib --jobs 8 --package-workers 2
```

`--jobs` is the total compiler budget. `--package-workers` limits simultaneous
native package builds, defaulting to two. Ready packages share that budget;
a lone ready package gets all available jobs. Target dependencies finish before
their consumers start. Runtime-only cycles do not serialize compilation. Use
`--package-workers 1` for sequential packages, or `--jobs 2` on a small machine.
Bootstrap, loader and app stages retain their existing ordering; this scheduler
parallelizes native packages, not independent Rust applications.

Native package cache keys follow each recipe's Python imports and their local
helpers, package resources, source pins, seed tools, bootstrap receipts and
native dependency archives. Computed Python imports use a conservative dependency
boundary for their consumer. Stage receipts track their own build code and inputs.
Adding an app or editing deployment/VM helpers does not invalidate native OS
packages. Timestamps or an identical file replacement alone do not invalidate a
stage; content, modes, ownership and symlink changes still do. The cache policy
upgrade requires one rebuild of existing native packages and stage receipts.

For optional C/C++ object reuse, the native package builder detects an existing
host `ccache`, or accepts an explicit executable:

```sh
DISTRO_CCACHE=/path/to/ccache python3 build.py build busybox zlib --jobs 8
DISTRO_CCACHE=0 ./run                         # Disable compiler caching
CCACHE_DIR="$PWD/out/compiler-cache" ccache --show-stats
```

The executable hash is recorded as a declared host seed. Objects stay in
`out/compiler-cache/` with a 5 GB limit. Builds hash the real compiler, toolchain
receipts and dependency bytes, use preprocessor mode, and exclude inherited
ccache configuration. They keep target sysroot checks in front of cache dispatch.
No host software is installed automatically. Without ccache, compilation runs
normally; Rust continues using its existing Cargo workspaces. Object-cache hits
also depend on compiler arguments and source paths; there is no promised speedup
for a first build. See the [ccache manual](https://ccache.dev/manual/latest.html)
for its cache behavior and statistics.

Keep build outputs between normal iterations. Use `dev` for app-only updates,
`vm --use` for reopening a VM, and `clean` when you need a full rebuild. A fresh
source build still compiles the bootstrap and kernel; these changes do not replace
source builds with prebuilt OS packages.

## Clean for a fresh build

Stop builds, deployments and QEMU before cleaning.

Preview cleanup, then remove the generated toolchain, SDK, packages, application
compiler caches (including `out/compiler-cache/`), images, staging trees, build logs and build receipts:

```sh
./clean --dry-run
./clean
./run --profile desktop-dev --name fresh-dev
```

`./clean` is a shell launcher that forwards to `python3 build.py clean`. Cleanup
refuses to delete outputs when an existing build, deployment or saved-VM lock is
held. Do not start another build during cleanup. Persistent lock files remain so
the builder and cleaner continue to share the same locks.

Cleanup prints each target as it starts and finishes, with a live terminal bar,
elapsed time and a count of removed entries (files, directories and symlinks).
The percentage measures **completed top-level cleanup targets**, not bytes or
estimated time. Inside a large target, the entry counter and sampled current
path keep updating without a separate counting pass. Redirected/piped stderr
gets plain progress lines instead of terminal control sequences.

Live progress goes to stderr; stdout remains the final JSON summary, including
`entries_removed`, `seconds` and the saved `log` path. Every real cleanup saves a
unique timestamped log under `out/verification/clean/`, which survives cleanup.
Deletion failures and Ctrl+C record partial progress and the failure/interruption;
they do not report successful completion. Lock/path preflight failures happen
before deletion or log creation. `--dry-run` only prints the plan and creates no
cleanup log. An absent `out/` directory likewise needs no log or cleanup.

Source checkouts and local edits under `sources/` stay intact. Cleanup also
preserves downloaded archives under `out/sources/downloads/`, saved disks under
`out/vms/`, evidence under `out/verification/`, source bundles, contribution
checkouts and unrecognized output directories. It only deletes the pipeline's
known generated outputs; it does not run `git clean` or touch host caches.

Optional flags:

```sh
./clean --downloads             # Also remove downloaded source archives
./clean --vms                   # Also delete saved VM disks, data and firmware variables
./clean --downloads --vms       # Rebuild and download again with no saved VMs
./clean --all                   # Same as --downloads --vms
```

The next build must recreate the toolchain and packages and can take substantial
time. Retaining archives avoids downloading them again; Cargo caches inside
generated app/loader directories are removed. Reusing a preserved VM name still
opens its original disk, so use a new name to test the freshly rebuilt base.
The cleaner does not repair cache invalidation or speed up ordinary iteration;
use `dev` for app updates and `vm --use` to reopen a saved disk.

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
| Firefox IPDL reports `AF_UNIX path too long` | Its Python multiprocessing socket exceeded Linux's path limit. The Firefox recipe uses a private short temporary directory under `out/tmp/`; retry `./run` with the corrected recipe. Matching completed packages are reused. |
| Black QEMU window on WSL despite successful guest boot | Check `weston.log` for shared-memory transport errors; see [blank WSLg windows](setup.md#blank-or-black-wslg-windows) |
| Low visible frame rate on WSL | Verify `.wslgconfig`, `weston.log`, Windows/guest display rates and KVM separately |
| TCG instead of KVM | Read the launcher's probe failure and complete [KVM access setup](setup.md#kvm-access), including a new login after group changes; do not run the builder as root |
| Rebuilt changes absent | Confirm the VM name and deployment receipt; a saved disk is not replaced by image composition |
| Missing guest-agent socket | Boot a `desktop-dev` image with `--use --development` and the matching name |
| Missing image | Use `run` to build it; `vm` only launches existing media |
| QEMU Guest Agent cannot create its Python venv | Install `python3-venv` and rerun `doctor`; see [host prerequisites](setup.md#check-host-prerequisites) |
| Cargo feature/API mismatch | Rerun online to synchronize component main branches; if it persists, inspect actual source compatibility and preserve local edits |
| Guest install fails | Inspect `deployment.json`; failed transactions/restarts do not imply rollback |
| Build fails | Read its reported log and fix the owning source/recipe, then retry |

If the OS packages stage stops at GLib with `Dependency "iconv" not found`, read
the package log under `out/logs/packages/` and the Meson log it names. A nested
`libgcc_s.so.1 ... not found` error indicates a cross-compiler configuration
failure. The Meson adapter clears the inherited `CXX` variable for target builds
and selects C++ explicitly through its sysroot wrapper only for recipes that
require it. GLib's C build can then detect glibc's built-in iconv without adding
host libraries. Retry `./run` after updating the build configuration; completed
bootstrap stages and matching package builds are reused. To check GLib alone
after the bootstrap is ready, use `python3 build.py build glib`.

For CLI details use `python3 build.py --help`, `run --help`, `clean --help`, `dev --help`, `deploy --help`,
`vm --help` or the individual stage help. Source snapshots, packages and logs
remain under `out/`; [architecture](architecture.md) maps their locations.
