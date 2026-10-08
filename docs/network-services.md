# Source-built network services

The target networking recipes are independent modules:

| Package | Version | Purpose |
| --- | --- | --- |
| libnl | 3.12.0 | Netlink route/generic/netfilter/diagnostic libraries |
| libndp | 1.9 | IPv6 neighbor discovery library |
| wpa-supplicant | 2.12 | nl80211 wireless authentication and D-Bus control |
| networkmanager | 1.58.1 | NetworkManager daemon, dispatcher and libnm D-Bus client API |

The libnl checksum matches its upstream
[release checksum](https://github.com/thom311/libnl/releases/download/libnl3_12_0/libnl-3.12.0.tar.gz.sha256sum).
NetworkManager's archive and checksum come from the upstream release API linked
by its [release page](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/releases).
WPA supplicant is the archive linked by the upstream
[download page](https://w1.fi/wpa_supplicant/), fetched over verified HTTPS and
pinned by SHA-256. No claim of verified WPA release PGP signature is made.

The libndp website currently fails TLS hostname verification. Its recipe uses
the upstream [GitHub v1.9 tag](https://github.com/jpirko/libndp/tree/v1.9), fetched
over verified HTTPS and pinned by SHA-256. Since that archive omits generated
configure files, the recipe builds pinned GNU Autoconf, Automake and Libtool
generators into its private native prefix. Host GCC, Perl and m4 are declared
generator seeds; those tools and their libraries do not enter the target image.

Target builds use the source-built compiler, a package dependency sysroot and
explicit pkg-config paths. The compiler wrapper refuses host include/library
paths. Libtool receives the private sysroot during install relinking. Exported
ELFs must have complete target library closure and no RPATH/RUNPATH. Runtime
probes check the target loader's library resolution before running binaries.

NetworkManager revision 2 preserves the upstream initrd generator's unit masking
and activation behavior using constant POSIX word lists in place of Bash arrays.
The overlay checks the exact upstream source hash and records its resulting
hash; both system and initrd modes run against the source-built static BusyBox
in private directories. This avoids installing a boot generator with an absent
interpreter. The package explicitly depends on BusyBox for its shell utilities.

Revision 3 uses standard pkg-config sysroot semantics for directory variables:
Meson receives `/usr/share/polkit-1/actions` as the target policy installation
directory, while compiler include/library flags remain rooted in the private
dependency sysroot. The build rejects staging paths outside `/usr`, `/etc` and
`/var`, and rejects build-work/sysroot paths in installed units, configuration
and other text payload. Original ELF source filename strings are compile
provenance; ELF runtime library paths are checked separately by the loader audit.

WPA supplicant includes nl80211, WPA3 SAE/OWE, IEEE 802.1X TLS/PEAP/TTLS and the
modern `fi.w1.wpa_supplicant1` D-Bus API. It uses the source-built OpenSSL
package; the package contains no network credentials and does not enable or
start a service during installation.

`packaging.network_service_test.qualify_wpa` starts the exact packaged WPA
daemon and D-Bus daemon/client on an EXTERNAL-authenticated private bus. It
queries the real `Interfaces` property and requires an empty array. No physical
interface is attached, scanned or configured. Test output lives below
`out/network-tests`; `CUSTOM_WPA_DBUS_OK` proves IPC and library closure, not
physical Wi-Fi association or kernel/firmware support.

After the canonical WPA/native toolkit packages are built, run
`python3 tools/qualify-network-crypto.py wpa` on a worker that permits private
Unix sockets. This creates a fresh qualification receipt under `out/`.

The initial NetworkManager profile uses systemd session tracking/journal,
polkit authorization, keyfile settings, internal DHCP, and WPA supplicant as its
Wi-Fi backend. Connectivity probing is compiled but disabled by default in its
configuration. DNS updates use the normal generated guest's `resolv.conf`.
Neither build nor host-kernel qualification starts NetworkManager on the host
network; actual network management must be tested in a disposable guest.

The minimum NetworkManager build selects upstream `crypto=null`; WPA TLS
authentication still uses OpenSSL in the supplicant. NetworkManager's own
certificate/private-key import and inspection features and enterprise Wi-Fi
integration are not qualified. A complete certificate-import profile needs a
source-built NSS/NSPR or GnuTLS dependency stack. It also omits nmcli/nmtui,
PPP, mobile broadband, Bluetooth, OVS, CLAT and optional bindings. The Telorgon
integration uses the D-Bus API. Enabling nmcli additionally requires a packaged
readline or libedit stack; nmtui requires its terminal UI libraries.

Hotspot firewall/DNS helpers and physical wireless kernel modules/firmware are
not supplied by these minimum recipes. Those capabilities must be added as
ordinary packages and qualified explicitly before a profile advertises them.
