#!/usr/bin/env python3
"""Cross-compile pacman against this distro's source-built dependency sysroot."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.packaging.target_build import build_target
build_target("pacman")
