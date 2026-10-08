#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.dconf_native import build_desktop_settings
build_desktop_settings("gsettings-desktop-schemas")
