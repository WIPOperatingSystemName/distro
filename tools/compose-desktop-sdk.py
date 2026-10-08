#!/usr/bin/env python3
"""Compose the Telorgon cross-build SDK from real source-built ALPM artifacts."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / 'src'))
from distro_build.graph import plan
from distro_build.model import catalog
from distro_build.packaging import PacmanToolkit
from distro_build.apps import preflight

selected = ['libffi','expat','wayland','wayland-protocols','libdrm','libxkbcommon','xkeyboard-config',
            'libevdev','mtdev','libudev','libseat','libinput','mesa-gbm','libgcc','libstdc++','pipewire','dbus']
records = [json.loads((project / 'out/state/packages' / f'{r.name}.json').read_text())
           for r in plan(catalog(project), selected)]
validation = project / 'out/bootstrap/root/.bootstrap-validated.json'
identity = hashlib.sha256(json.dumps({'packages':{r['package']:r['sha256'] for r in records},
    'header_validation':hashlib.sha256(validation.read_bytes()).hexdigest(),
    'composer':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()
output = project / 'out/sdk' / identity
root = output / 'sysroot'
output.mkdir(parents=True,exist_ok=True)
if root.exists():
    shutil.rmtree(root)
PacmanToolkit(project / 'out/native-toolkit/prefix').install(root,[Path(r['path']) for r in records],
    bootstrap=True,expected_hashes={Path(r['path']).name:r['sha256'] for r in records})
shutil.copytree(project / 'out/bootstrap/root/usr/include',root / 'usr/include',symlinks=True,dirs_exist_ok=True)
shutil.copy2(validation,root / validation.name)
report = {'kind':'sourcebuilt-desktop-build-sdk','identity':identity,'sysroot':str(root),
    'packages':records,'headers':'validated source-built bootstrap headers; development-only SDK inputs',
    'preflight':preflight(project,root),'qualification':'build SDK; this is not a complete runtime image'}
(output / 'sdk.json').write_text(json.dumps(report,indent=2)+'\n')
(project / 'out/sdk/current.json').write_text(json.dumps({'identity':identity,'sysroot':str(root),
    'report':str(output / 'sdk.json')},indent=2)+'\n')
print(json.dumps({'sysroot':str(root),'report':str(output / 'sdk.json'),'preflight':report['preflight']},indent=2))
