"""Ordinary desktops must not inherit the qualification fixture."""
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.compose import system_policy_package
from distro_build.model import BuildError


class DesktopUsePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        for tree in ("systemd", "desktop", "desktop-use"):
            shutil.copytree(source / "system" / tree, self.project / "system" / tree, symlinks=True)
        shutil.copy2(source / "LICENSE", self.project / "LICENSE")

    def test_normal_policy_has_a_session_and_no_qualification_payload(self):
        package = system_policy_package(self.project, "desktop-use")
        with tarfile.open(package) as archive:
            members = {member.name.lstrip("./"): member for member in archive}
            self.assertNotIn("usr/libexec/custom-distro/desktop-session-probe", members)
            self.assertFalse(any("-check.service" in name or name.split("/")[-1].startswith("check-") for name in members))
            link = members["etc/systemd/system/graphical.target.wants/custom-distro-desktop.service"]
            self.assertTrue(link.issym())
            self.assertEqual(link.linkname, "../custom-distro-desktop.service")
            unit = archive.extractfile(members["etc/systemd/system/custom-distro-desktop.service"]).read().decode()
            self.assertIn("User=custom", unit)
            self.assertIn("PAMName=login", unit)
            self.assertIn("ExecStart=/usr/lib/custom-distro/start-desktop-session", unit)
            for member in members.values():
                if member.isfile() and member.name.startswith("usr/lib/custom-distro/"):
                    data = archive.extractfile(member).read()
                    self.assertNotIn(b"CUSTOM_DESKTOP_PROBE", data)
                    self.assertNotIn(b"WAYLAND_DEBUG=client", data)
                    self.assertNotIn(b"telorgon-file-explorer --show", data)

    def test_qualification_profile_still_requires_its_actual_probe(self):
        with self.assertRaisesRegex(BuildError, "qualification client is missing"):
            system_policy_package(self.project, "desktop")


if __name__ == "__main__":
    unittest.main()
