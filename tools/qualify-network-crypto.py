#!/usr/bin/env python3
"""Qualify canonical source-built TLS, signature and WPA IPC artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from distro_build.packaging.network_test import qualify_curl
from distro_build.packaging.network_service_test import qualify_wpa
from distro_build.packaging.signature_test import qualify_signatures
from distro_build.packaging.signed_test import prepare_signed_fixture
from distro_build.packaging.toolkit import PacmanToolkit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checks", nargs="*", choices=("tls", "signatures", "wpa"),
                        help="Default: all three; requires canonical built packages/native toolkit")
    options = parser.parse_args()
    checks = options.checks or ["tls", "signatures", "wpa"]
    run = uuid.uuid4().hex[:16]
    records = {}
    for check in dict.fromkeys(checks):
        if check == "tls":
            directory = PROJECT / "out/network-tests" / ("tls-" + run)
            qualify_curl(PROJECT, directory)
        elif check == "wpa":
            directory = PROJECT / "out/network-tests" / ("wpa-" + run)
            qualify_wpa(PROJECT, directory)
        else:
            fixture = prepare_signed_fixture(PROJECT / "out/signing-tests" / ("fixture-" + run),
                PacmanToolkit(PROJECT / "out/native-toolkit/prefix"))
            directory = PROJECT / "out/signing-tests" / ("verifier-" + run)
            qualify_signatures(PROJECT, directory, fixture)
        records[check] = str(directory / "qualification.json")
    print(json.dumps({"schema": 1, "scope": "private host-kernel target runtime probes",
                      "qualification_receipts": records, "guest_alpm_verified_by_this_tool": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
