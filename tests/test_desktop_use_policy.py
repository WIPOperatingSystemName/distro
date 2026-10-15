"""Ordinary desktops must not inherit the qualification fixture."""
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.compose import system_policy_package
from distro_build.model import BuildError


class DesktopUsePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        for tree in ("systemd", "desktop", "desktop-use", "system-shell"):
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

    def test_system_shell_policy_boots_a_dedicated_account_without_a_user_session(self):
        package = system_policy_package(self.project, "desktop-dev", system_shell=True)
        with tarfile.open(package) as archive:
            members = {member.name.lstrip("./"): member for member in archive}
            self.assertNotIn("etc/systemd/system/graphical.target.wants/custom-distro-desktop.service", members)
            link = members["etc/systemd/system/graphical.target.wants/telorgon-shell.service"]
            self.assertTrue(link.issym())
            self.assertEqual(link.linkname, "../telorgon-shell.service")
            unit = archive.extractfile(members["etc/systemd/system/telorgon-shell.service"]).read().decode()
            self.assertIn("User=telorgon-shell", unit)
            self.assertIn("ExecStart=/usr/bin/telorgon-test-shell --system-shell", unit)
            self.assertIn("RuntimeDirectoryMode=0700", unit)
            self.assertIn("ProtectHome=yes", unit)
            self.assertIn("Type=dbus", unit)
            self.assertIn("BusName=org.telorgon.SystemShell1", unit)
            self.assertIn("ExecStartPost=+/usr/bin/busctl call org.telorgon.SystemShell1", unit)
            self.assertNotIn("PAMName=", unit)
            self.assertNotIn("user@1000", unit)
            account = archive.extractfile(members["etc/sysusers.d/telorgon-shell.conf"]).read().decode()
            self.assertIn('u telorgon-shell - "Telorgon system shell" /var/lib/telorgon-shell /bin/false', account)
            users = archive.extractfile(members["etc/telorgon/login-users"]).read().decode()
            self.assertEqual([line for line in users.splitlines() if line and not line.startswith("#")], ["custom"])
            policy = ET.fromstring(archive.extractfile(members["usr/share/dbus-1/system.d/org.telorgon.SystemShell1.conf"]).read())
            self.assertIsNotNone(policy.find("policy[@context='default']/deny[@send_destination='org.telorgon.SystemShell1']"))
            self.assertIsNotNone(policy.find("policy[@user='root']/allow[@send_destination='org.telorgon.SystemShell1']"))
            self.assertIsNotNone(policy.find("policy[@user='telorgon-shell']/allow[@own='org.telorgon.SystemShell1']"))

    def test_system_shell_authentication_is_separate_and_passwords_are_not_enrolled(self):
        package = system_policy_package(self.project, "desktop-dev", system_shell=True)
        with tarfile.open(package) as archive:
            def read(path):
                return archive.extractfile(path).read().decode()
            manager = read("etc/systemd/system/telorgon-session-manager.service")
            self.assertIn("BusName=org.telorgon.Session1", manager)
            self.assertIn("ExecStart=/usr/bin/telorgon-test-shell --session-manager", manager)
            self.assertNotIn("User=custom", manager)
            self.assertIn("telorgon-session-manager.service", read("etc/systemd/system/telorgon-shell.service"))
            renderer = read("etc/systemd/system/telorgon-shell.service")
            self.assertIn("Wants=telorgon-session-manager.service", renderer)
            self.assertFalse(any("telorgon-session-manager.service" in line for line in renderer.splitlines() if line.startswith("Requires=")))
            pam = read("etc/pam.d/telorgon-session")
            for line in ("auth required pam_unix.so", "account required pam_unix.so", "session required pam_systemd.so"):
                self.assertIn(line, pam)
            self.assertNotIn("pam_permit", pam)
            self.assertNotIn("nullok", pam)
            policy = ET.fromstring(read("usr/share/dbus-1/system.d/org.telorgon.Session1.conf"))
            self.assertIsNotNone(policy.find("policy[@context='default']/deny[@send_destination='org.telorgon.Session1']"))
            self.assertIsNotNone(policy.find("policy[@user='telorgon-shell']/allow[@send_destination='org.telorgon.Session1']"))
            self.assertIsNotNone(policy.find("policy[@user='root']/allow[@own='org.telorgon.Session1']"))
            self.assertNotIn("etc/shadow", archive.getnames())

    def test_system_shell_is_rejected_for_non_desktop_images(self):
        with self.assertRaisesRegex(BuildError, "requires a desktop"):
            system_policy_package(self.project, "systemd", system_shell=True)


if __name__ == "__main__":
    unittest.main()
