#!/usr/bin/env python3
"""Build PipeWire graph/server support without host multimedia libraries."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.runtime_native import build_runtime
build_runtime("pipewire")
