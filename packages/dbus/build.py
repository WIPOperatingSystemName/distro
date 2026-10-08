#!/usr/bin/env python3
"""Build the source-pinned D-Bus daemon against the distro sysroot."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.runtime_native import build_runtime
build_runtime("dbus")
