#!/usr/bin/env python3
"""Cross compile ALSA without host audio libraries or Python bindings."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.desktop_native import DesktopBuild

build = DesktopBuild()
build.autotools(["--disable-static", "--disable-topology", "--disable-python", "--with-configdir=/usr/share/alsa"])
build.license("alsa-lib", ["COPYING"])
for path in build.stage.rglob("*.la"):
    path.unlink()
build.audit()
