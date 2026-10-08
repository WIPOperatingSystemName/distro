#!/usr/bin/env python3
"""Install the source-pinned TLS trust data and its license without host trust."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.crypto_native import build_crypto
build_crypto("ca-certificates")
