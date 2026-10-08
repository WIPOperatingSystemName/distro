#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from distro_build.network_native import build_network
build_network("networkmanager")
