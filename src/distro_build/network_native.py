"""Source-built network services; no host network configuration is changed."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from .desktop_native import DesktopBuild
from .desktop_services import ServiceBuild
from .runtime_native import RuntimeBuild


class NetworkBuild(DesktopBuild):
    closed_loader = RuntimeBuild.closed_loader
    closed_probe = RuntimeBuild.closed_probe
    glib_generators = ServiceBuild.glib_generators
    gettext_generators = ServiceBuild.gettext_generators

    def __init__(self) -> None:
        super().__init__()
        # pkgconf's default sysroots arbitrary directory variables too. Meson
        # uses policydir as a target install path, so use standard pkg-config
        # semantics: sysroot compiler flags, leave directory variables intact.
        self.env["PKG_CONFIG_FDO_SYSROOT_RULES"] = "1"

    def audit_installed_paths(self) -> None:
        forbidden = [str(self.work).encode(), str(self.sysroot).encode(), b"/out/work/"]
        inspected = []
        for path in sorted(self.stage.rglob("*")):
            relative = path.relative_to(self.stage)
            if relative.parts[0] not in {"usr", "etc", "var"}:
                raise RuntimeError(f"Network package installs outside target prefixes: {relative}")
            if path.is_symlink() or not path.is_file():
                continue
            content = path.read_bytes()
            # ELF __FILE__ strings identify original source inputs; audit runtime
            # search paths separately through readelf/explicit loader checks.
            if content.startswith(b"\x7fELF"):
                continue
            if any(marker in content for marker in forbidden):
                raise RuntimeError(f"Installed network unit/config/data leaks a build path: {relative}")
            inspected.append(str(relative))
        (self.work / "installed-path-audit.json").write_text(json.dumps({
            "target_prefixes": ["/usr", "/etc", "/var"], "text_files": inspected,
            "pkgconfig_directory_semantics": "PKG_CONFIG_FDO_SYSROOT_RULES=1",
            "host_sysroot_leaks": False}, indent=2) + "\n")

    def initrd_shell_overlay(self) -> None:
        """Keep the upstream generator's unit policy with the packaged POSIX shell."""
        path = self.source / "src/nm-initrd-generator/nm-initrd-generator.sh"
        original = path.read_bytes()
        if hashlib.sha256(original).hexdigest() != "270cb4cbd9732aa253b9a0895b50f09fac1dcb5b252016e5b627f75b4e423876":
            raise RuntimeError("Pinned NetworkManager initrd generator changed; review its shell overlay")
        content = original.decode()
        replacements = [
            ("#!/bin/bash", "#!/bin/sh", 1),
            ("initrd_units=(\n    NetworkManager-config-initrd.service\n    NetworkManager-initrd.service\n    NetworkManager-wait-online-initrd.service\n)",
             "initrd_units='NetworkManager-config-initrd.service NetworkManager-initrd.service NetworkManager-wait-online-initrd.service'", 1),
            ("host_units=(\n    NetworkManager.service\n    NetworkManager-dispatcher.service\n    NetworkManager-wait-online.service\n)",
             "host_units='NetworkManager.service NetworkManager-dispatcher.service NetworkManager-wait-online.service'", 1),
            ('"${initrd_units[@]}"', '${initrd_units}', 2),
            ('"${host_units[@]}"', '${host_units}', 1),
        ]
        for old, new, count in replacements:
            if content.count(old) != count:
                raise RuntimeError("Unexpected NetworkManager generator overlay match count")
            content = content.replace(old, new)
        path.write_text(content)
        (self.work / "initrd-shell-overlay.json").write_text(json.dumps({
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "patched_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "change": "Bash arrays to constant POSIX word lists; same unit masking and links",
            "interpreter": "/bin/sh", "runtime_provider": "source-built static BusyBox"}, indent=2) + "\n")

    def verify_initrd_generator(self) -> None:
        binary = self.sysroot / "bin/busybox"
        # BusyBox is a static target package, so no host loader is involved.
        program = subprocess.check_output([str(self.readelf), "-l", str(binary)], text=True)
        if "Requesting program interpreter" in program:
            raise RuntimeError("NetworkManager generator probe requires the static target BusyBox")
        tools = self.work / "generator-tools"
        tools.mkdir()
        for name in ("ln", "mkdir"):
            (tools / name).symlink_to(binary)
        script = self.stage / "usr/lib/systemd/system-generators/nm-initrd-generator.sh"
        for initial in (False, True):
            directory = self.work / ("generator-initrd" if initial else "generator-system")
            directory.mkdir()
            environment = dict(self.env, PATH=str(tools), SYSTEMD_IN_INITRD="1" if initial else "0")
            subprocess.run([str(binary), "sh", str(script), str(directory)], env=environment, check=True)
            units = (["NetworkManager", "NetworkManager-dispatcher", "NetworkManager-wait-online"] if initial
                     else ["NetworkManager-config-initrd", "NetworkManager-initrd", "NetworkManager-wait-online-initrd"])
            if any(not (directory / (unit + ".service")).is_symlink() or
                   os.readlink(directory / (unit + ".service")) != "/dev/null" for unit in units):
                raise RuntimeError("Target initrd generator failed to mask the expected unit set")
            if initial and not (directory / "network-online.target.wants/NetworkManager-wait-online-initrd.service").is_symlink():
                raise RuntimeError("Target initrd generator did not link its online wait unit")
        print("CUSTOM_NM_INITRD_GENERATOR_OK", flush=True)

    def autotools_generators(self) -> None:
        environment = self.host_env()
        environment["PATH"] = str(self.native / "bin") + os.pathsep + environment["PATH"]
        for name in ("autoconf", "automake", "libtool"):
            source = self.secondary(name)
            directory = self.work / ("native-" + name)
            directory.mkdir()
            options = [f"--prefix={self.native}"]
            if name == "libtool":
                options += ["--disable-ltdl-install", "--disable-static"]
            self.host_run([str(source / "configure"), *options], directory, environment)
            self.host_run(["make", f"-j{self.jobs}"], directory, environment)
            self.host_run(["make", "install"], directory, environment)
        self.env["PATH"] = str(self.native / "bin") + os.pathsep + self.env["PATH"]
        (self.work / "native-autotools-policy.json").write_text(json.dumps(
            {"generators": ["autoconf", "automake", "libtool"], "sources": "recipe-pinned secondary inputs",
             "declared_host_seeds": ["gcc", "perl", "m4"], "native_prefix": str(self.native),
             "target_payload_contains_generators": False}, indent=2) + "\n")


def build_network(name: str) -> None:
    build = NetworkBuild()
    if name == "libnl":
        build.autotools(["--disable-static", "--disable-cli", "--sysconfdir=/etc",
                         f"--with-sysroot={build.sysroot}"])
        build.license(name, ["COPYING"])
    elif name == "libndp":
        # The upstream tag archive needs the declared host autotools generators;
        # all resulting target compilation still uses the closed compiler.
        build.autotools_generators()
        build.run([str(build.native / "bin/autoreconf"), "-fi"])
        build.autotools(["--disable-static", f"--with-sysroot={build.sysroot}"])
        build.license(name, ["COPYING"])
    elif name == "wpa-supplicant":
        directory = build.source / "wpa_supplicant"
        configuration = """CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CONFIG_DRIVER_WIRED=y
CONFIG_IEEE8021X_EAPOL=y
CONFIG_EAP_TLS=y
CONFIG_EAP_PEAP=y
CONFIG_EAP_TTLS=y
CONFIG_EAP_MSCHAPV2=y
CONFIG_EAP_GTC=y
CONFIG_TLS=openssl
CONFIG_PKCS12=y
CONFIG_CTRL_IFACE=y
CONFIG_CTRL_IFACE_DBUS_NEW=y
CONFIG_CTRL_IFACE_DBUS_INTRO=y
CONFIG_BACKEND=file
CONFIG_IPV6=y
CONFIG_IEEE80211R=y
CONFIG_IEEE80211W=y
CONFIG_SAE=y
CONFIG_OWE=y
CONFIG_AP=y
CONFIG_P2P=y
CONFIG_BGSCAN_SIMPLE=y
CONFIG_DEBUG_SYSLOG=y
CONFIG_IEEE80211AC=y
CONFIG_IEEE80211AX=y
CONFIG_IEEE80211BE=y
"""
        (directory / ".config").write_text(configuration)
        build.env["CFLAGS"] += " -I" + str(build.sysroot / "usr/include/libnl3")
        build.run(["make", f"-j{build.jobs}", "wpa_supplicant", "wpa_cli", "wpa_passphrase"], directory)
        for binary in ("wpa_supplicant", "wpa_cli", "wpa_passphrase"):
            target = build.stage / "usr/bin" / binary
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / binary, target)
            target.chmod(0o755)
        for filename in ("dbus-wpa_supplicant.conf",):
            destination = build.stage / "usr/share/dbus-1/system.d" / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / "dbus" / filename, destination)
        service = build.stage / "usr/share/dbus-1/system-services/fi.w1.wpa_supplicant1.service"
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text("[D-BUS Service]\nName=fi.w1.wpa_supplicant1\nExec=/usr/bin/wpa_supplicant -u -s -O /run/wpa_supplicant\nUser=root\nSystemdService=wpa_supplicant.service\n")
        unit = build.stage / "usr/lib/systemd/system/wpa_supplicant.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("[Unit]\nDescription=WPA wireless authentication service\nAfter=dbus.service\n\n[Service]\nType=dbus\nBusName=fi.w1.wpa_supplicant1\nExecStart=/usr/bin/wpa_supplicant -u -s -O /run/wpa_supplicant\n\n[Install]\nWantedBy=multi-user.target\nAlias=dbus-fi.w1.wpa_supplicant1.service\n")
        build.license(name, ["COPYING"])
        build.closed_probe(build.stage / "usr/bin/wpa_supplicant", "-v")
        (build.work / "wireless-build-policy.json").write_text(json.dumps(
            {"nl80211": True, "dbus_control": True, "openssl": True, "wpa3_sae": True,
             "physical_wireless_qualified": False, "host_network_touched": False}, indent=2) + "\n")
    elif name == "networkmanager":
        # Resolve compiled generators before executing them. Pure Python SDK
        # generators use the packaged GLib modules and declared host Python.
        for generator in ("glib-compile-resources", "glib-compile-schemas"):
            build.closed_loader(build.sysroot / "usr/bin" / generator)
        build.glib_generators()
        build.gettext_generators()
        build.initrd_shell_overlay()
        build.meson(["-Dsystemdsystemunitdir=/usr/lib/systemd/system",
                     "-Dsystemdsystemgeneratordir=/usr/lib/systemd/system-generators",
                     "-Ddbus_conf_dir=/usr/share/dbus-1/system.d", "-Dudev_dir=/usr/lib/udev",
                     "-Druntime_dir=/run", "-Dsession_tracking=systemd", "-Dsuspend_resume=systemd",
                     "-Dsession_tracking_consolekit=false", "-Dsystemd_journal=true",
                     "-Dpolkit=true", "-Dconfig_auth_polkit_default=true",
                     "-Dselinux=false", "-Dlibaudit=no", "-Dcrypto=null", "-Dlibpsl=false",
                     "-Dnmcli=false", "-Dnmtui=false", "-Dnm_cloud_setup=false",
                     "-Dmodem_manager=false", "-Dppp=false", "-Dovs=false", "-Dteamdctl=false",
                     "-Diwd=false", "-Dwifi=true", "-Dwext=false", "-Dnbft=false", "-Dclat=false",
                     "-Debpf=false", "-Dbluez5_dun=false", "-Dfirewalld_zone=false",
                     "-Dconcheck=true", "-Ddhcpcd=no", "-Dconfig_dhcp_default=internal",
                     "-Dconfig_wifi_backend_default=wpa_supplicant", "-Difupdown=false",
                     "-Dresolvconf=no", "-Dnetconfig=no", "-Dconfig_dns_rc_manager_default=file",
                     "-Diptables=/usr/sbin/iptables", "-Dip6tables=/usr/sbin/ip6tables",
                     "-Dnft=/usr/sbin/nft", "-Ddnsmasq=/usr/sbin/dnsmasq", "-Dmodprobe=/sbin/modprobe",
                     "-Dintrospection=false", "-Dvapi=false", "-Dqt=false", "-Dreadline=none",
                     "-Ddocs=false", "-Dman=false", "-Dtests=no", "-Dmore_logging=false"])
        configuration = build.stage / "etc/NetworkManager/NetworkManager.conf"
        configuration.parent.mkdir(parents=True, exist_ok=True)
        configuration.write_text("[main]\nplugins=keyfile\nauth-polkit=true\ndhcp=internal\nrc-manager=file\n\n[logging]\nbackend=journal\n\n[connectivity]\nenabled=false\n")
        build.license(name, ["COPYING", "COPYING.LGPL", "COPYING.GFDL"])
        build.closed_probe(build.stage / "usr/sbin/NetworkManager", "--version")
        build.verify_initrd_generator()
        document = build.stage / "usr/share/doc/networkmanager/build-capabilities.json"
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text(json.dumps({"dbus_api": True, "libnm": True, "wifi_backend": "wpa_supplicant",
            "supplicant_crypto": "source-built OpenSSL", "networkmanager_crypto": "null",
            "certificate_import_and_inspection_qualified": False, "enterprise_wifi_qualified": False,
            "physical_wireless_qualified": False, "nmcli": False, "nmtui": False,
            "hotspot_firewall_helpers_packaged": False, "host_network_touched": False}, indent=2) + "\n")
    else:
        raise RuntimeError(f"Unknown source-built network package: {name}")
    for path in build.stage.rglob("*.la"):
        path.unlink()
    build.audit_installed_paths()
    build.audit()
