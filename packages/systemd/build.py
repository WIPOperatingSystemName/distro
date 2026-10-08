#!/usr/bin/env python3
"""Build the source-built systemd target package with its declared sysroot."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.system_runtime import build_system_runtime
build_system_runtime('systemd')
