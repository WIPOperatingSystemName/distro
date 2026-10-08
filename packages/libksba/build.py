#!/usr/bin/env python3
"""Compile libksba using only the source-built target dependency sysroot."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.crypto_native import build_crypto
build_crypto('libksba')
