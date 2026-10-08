# Source-built desktop native libraries

The target desktop foundation is described by the ordinary `package.toml`
recipes in `packages/`. Python dispatches them through
`src/distro_build/desktop_native.py`; the native pacman toolkit composes each
private target dependency sysroot before compilation. No installed host graphics
libraries are target dependencies.

The implemented recipes build libffi, Expat, Wayland, wayland-protocols, libdrm,
libxkbcommon, xkeyboard-config, libevdev, mtdev, eudev (`libudev`), seatd
(`libseat`), libinput and Mesa's GBM interface (`mesa-gbm`). Each source archive
has an immutable SHA256 pin and an upstream HTTPS location. Generated target
packages retain actual upstream license notices. The Mesa GBM profile needs the
validated source-built second-pass C++ compiler and the `libgcc`/`libstdc++`
packages.

Meson and Autotools receive the private target sysroot and pkg-config paths.
Compiler wrappers reject explicit host include/library paths. Private host
Wayland scanners, gperf and Python template generators are built or imported
from pinned source inputs and stay out of target packages. Host compilers and
Python used by these generators are declared bootstrap inputs.

For a fresh build, first build the native package toolkit and the bootstrap C/C++
compiler as described in `bootstrap.md`, then:

```sh
python3 build.py fetch libinput wayland wayland-protocols libxkbcommon libdrm libseat mesa-gbm
python3 build.py build libinput wayland wayland-protocols libxkbcommon libdrm libseat mesa-gbm --jobs 8
python3 tools/qualify-desktop.py --gbm
```

The qualifier composes actual package artifacts through libalpm and executes a
probe using the distro's own dynamic loader. It creates/destroys a Wayland
server, compiles a US keyboard layout using only the packaged XKB files, creates
udev and libinput contexts, and loads each desktop library. It checks the
linker's successful input paths and all loaded DSO paths, then saves a report
under `out/qualification/desktop-native/`. This is a native target API check;
QEMU or hardware tests must separately prove display scanout, input events,
seat permissions and a working compositor session.

This initial Mesa package provides the GBM ABI for Telorgon's existing CPU KMS
renderer. It has no Vulkan, OpenGL, EGL or Gallium drivers. The Telorgon source
snapshot uses its existing `Auto` renderer so that a missing GPU backend can
fall back to the CPU renderer. Physical GPU rendering requires additional Mesa
profiles and qualification; loading GBM alone is not evidence of GPU rendering.
The initial libinput profile omits optional libwacom and Lua plugins. eudev runs
as `/usr/sbin/udevd`, with `/etc/udev` configuration and packaged DRM/input rules
under `/usr/lib/udev/rules.d`. seatd's server and library are both packaged; the
session must launch the server and provide its socket to libseat.
