#!/usr/bin/env python3
"""Build and split the public systemd API from its pinned upstream source."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.system_runtime import build_system_runtime
build_system_runtime("libsystemd")
