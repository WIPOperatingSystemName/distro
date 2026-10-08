"""Source-built network and OpenPGP libraries for authenticated package updates."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .runtime_native import RuntimeBuild


class CryptoBuild(RuntimeBuild):
    def __init__(self) -> None:
        super().__init__()
        self.env["CC_FOR_BUILD"] = "/usr/bin/gcc"
        self.env["SYSROOT"] = str(self.sysroot)
        self.env["GPGRT_CONFIG"] = str(self.sysroot / "usr/bin/gpgrt-config")
        self.env["GPG_ERROR_CONFIG"] = "no"

    def prefixes(self, names: tuple[str, ...]) -> list[str]:
        return [f"--with-{name}-prefix={self.sysroot}/usr" for name in names]


def ca_certificates() -> None:
    stage = Path(os.environ["CD_STAGE_DIR"])
    source = Path(os.environ["CD_SOURCE_DIR"]) / "cacert-2026-09-25.pem"
    data = source.read_text()
    certificates = re.findall(r"-----BEGIN CERTIFICATE-----\s+([A-Za-z0-9+/=\s]+)-----END CERTIFICATE-----", data)
    if len(certificates) != 121:
        raise RuntimeError("Unexpected certificate count in the source-pinned Mozilla trust bundle")
    for certificate in certificates:
        der = base64.b64decode("".join(certificate.split()), validate=True)
        if not der.startswith(b"\x30"):
            raise RuntimeError("Invalid X.509 DER in pinned CA bundle")
    destination = stage / "etc/ssl/certs/ca-certificates.crt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    (stage / "etc/ssl/cert.pem").symlink_to("certs/ca-certificates.crt")
    license = stage / "usr/share/licenses/ca-certificates/MPL-2.0.txt"
    license.parent.mkdir(parents=True)
    shutil.copyfile(Path(os.environ["CD_SOURCES_DIR"]) / "license/MPL-2.0.txt", license)
    (license.parent / "NOTICE").write_text("Unmodified Mozilla CA trust data converted to PEM by curl's CA extraction service.\n"
                                          "Pinned source: https://curl.se/ca/cacert-2026-09-25.pem\n"
                                          "Mozilla name constraints are not carried in the converted PEM bundle.\n")


def build_crypto(name: str) -> None:
    if name == "ca-certificates":
        ca_certificates()
        return
    build = CryptoBuild()
    common = ["--disable-static", "--disable-rpath", "--disable-nls"]
    if name == "libgpg-error":
        build.env.pop("GPGRT_CONFIG", None)
        build.autotools(["--disable-static", "--disable-nls", "--disable-doc"])
        build.license(name, ["COPYING", "COPYING.LIB"])
    elif name == "libassuan":
        build.autotools([*common, *build.prefixes(("libgpg-error",))])
        build.license(name, ["COPYING", "COPYING.LIB"])
    elif name == "libgcrypt":
        build.autotools([*common, *build.prefixes(("libgpg-error",)), "--disable-doc"])
        build.license(name, ["COPYING", "COPYING.LIB"])
    elif name == "libksba":
        build.autotools([*common, *build.prefixes(("libgpg-error",))])
        build.license(name, ["COPYING", "COPYING.LGPLv3", "COPYING.GPLv3", "COPYING.GPLv2"])
    elif name == "npth":
        build.autotools(["--disable-static"])
        build.license(name, ["COPYING.LIB"])
    elif name == "gnupg":
        build.autotools([*build.prefixes(("libgpg-error", "libgcrypt", "libassuan", "libksba", "npth")),
                         "--sysconfdir=/etc", "--localstatedir=/var", "--disable-rpath", "--disable-nls",
                         "--disable-doc", "--disable-tests", "--disable-scdaemon", "--disable-dirmngr",
                         "--disable-keyboxd", "--disable-tpm2d", "--disable-wks-tools", "--disable-g13",
                         "--disable-sqlite", "--disable-ntbtls", "--disable-gnutls", "--disable-ldap",
                         "--disable-bzip2", "--disable-card-support", "--disable-ccid-driver",
                         "--without-readline", f"--with-zlib={build.sysroot}/usr",
                         "--enable-build-timestamp=2025-09-01T00:00:00", "--with-agent-pgm=/usr/bin/gpg-agent"])
        build.license(name, ["COPYING", "COPYING.CC0", "COPYING.GPL2", "COPYING.other", "COPYING.LGPL21", "COPYING.LGPL3"])
        build.closed_probe(build.stage / "usr/bin/gpg", "--version")
        build.closed_probe(build.stage / "usr/bin/gpgv", "--version")
    elif name == "gpgme":
        build.autotools(["--disable-static", f"--with-sysroot={build.sysroot}",
                         *build.prefixes(("libgpg-error", "libassuan")), "--enable-languages=",
                         "--enable-fixed-path=/usr/bin", "--disable-gpg-test", "--disable-gpgconf-test",
                         "--disable-gpgsm-test", "--disable-g13-test",
                         "--enable-build-timestamp=2025-09-01T00:00:00"])
        build.license(name, ["COPYING", "COPYING.LESSER"])
    elif name == "curl":
        build.autotools(["--disable-static", f"--with-sysroot={build.sysroot}", "--with-openssl",
                         "--with-zlib", "--without-libpsl", "--without-libidn2", "--without-brotli",
                         "--without-zstd", "--without-nghttp2", "--without-nghttp3", "--without-ngtcp2",
                         "--without-libssh", "--without-libssh2", "--without-gssapi", "--without-libgsasl",
                         "--disable-ldap", "--disable-ldaps", "--without-libuv", "--disable-manual",
                         "--disable-docs", "--with-ca-bundle=/etc/ssl/certs/ca-certificates.crt",
                         "--without-ca-path", "--without-ca-fallback", "--without-zsh-functions-dir",
                         "--without-fish-functions-dir"])
        build.license(name, ["COPYING"])
        version = build.closed_probe(build.stage / "usr/bin/curl", "--version")
        if "OpenSSL/3.5.9" not in version or "https" not in version:
            raise RuntimeError("Required source-built curl TLS backend/protocol missing")
        from .packaging.sdk_metadata import normalize_sdk_metadata
        metadata = normalize_sdk_metadata(build.stage, build.sysroot, name)
        (build.work / "sdk-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    else:
        raise RuntimeError(f"Unknown target crypto/network package: {name}")
    for path in build.stage.rglob("*.la"):
        path.unlink()
    # install-info writes a global index shared by otherwise independent GNU
    # libraries. It belongs to image documentation policy, not each package.
    (build.stage / "usr/share/info/dir").unlink(missing_ok=True)
    build.audit()
