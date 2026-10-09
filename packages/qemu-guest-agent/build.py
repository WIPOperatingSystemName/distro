#!/usr/bin/env python3
"""Build only QEMU Guest Agent, using the independent target dependency root."""
import os
from pathlib import Path
import shutil
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.desktop_native import DesktopBuild

build = DesktopBuild()
# QEMU's release bundles firmware submodules containing absolute symlinks.
# The agent never uses firmware. Exclude that subtree and apply Python's data
# extraction filter to every remaining member; never relax the global filter.
sources = build.work / "agent-source"
sources.mkdir()
with tarfile.open(build.source / "qemu-9.2.4.tar.xz") as archive:
    def agent_filter(member, destination):
        if member.name.startswith("qemu-9.2.4/roms/"):
            return None
        return tarfile.data_filter(member, destination)
    archive.extractall(sources, filter=agent_filter)
build.source = sources / "qemu-9.2.4"
directory = build.work / "target-build"
directory.mkdir()
build.env["PKG_CONFIG"] = "/usr/bin/pkg-config"
build.run([str(build.source / "configure"), "--prefix=/usr", "--libdir=/usr/lib",
           "--sysconfdir=/etc", "--localstatedir=/var", "--disable-download",
           "--without-default-features", "--disable-system", "--disable-user",
           "--disable-tools", "--disable-docs", "--disable-werror", "--disable-plugins",
           "--enable-guest-agent", "--disable-guest-agent-msi", "--target-list=",
           f"--cc={build.wrapper}", "--host-cc=/usr/bin/gcc", "--cpu=x86_64",
           f"--cross-prefix={build.tools}/bin/{build.target}-",
           "--extra-ldflags=-static-libgcc"], directory)
build.run(["ninja", "-C", str(directory), f"-j{build.jobs}", "qga/qemu-ga"], directory)
binary = build.stage / "usr/bin/qemu-ga"
binary.parent.mkdir(parents=True)
shutil.copy2(directory / "qga/qemu-ga", binary)
recipe = Path(__file__).parent
shutil.copytree(recipe / "files", build.stage, dirs_exist_ok=True, symlinks=True)
link = build.stage / "etc/systemd/system/multi-user.target.wants/custom-distro-development-agent.service"
link.parent.mkdir(parents=True, exist_ok=True)
link.symlink_to("/usr/lib/systemd/system/custom-distro-development-agent.service")
build.license("qemu-guest-agent", ["COPYING"])
build.audit()
build.probe(binary, "--version")
