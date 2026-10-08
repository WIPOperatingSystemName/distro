#!/usr/bin/env python3
"""Build the pinned mtdev target payload using the private dependency sysroot."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.desktop_native import build_desktop_native
build_desktop_native('mtdev')
