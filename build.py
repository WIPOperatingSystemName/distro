#!/usr/bin/env python3
"""Custom Distro's local and CI entry point."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from distro_build.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
