"""Native assembly interoperates with the source-built target libalpm client.

The guest boot test supplies the real -Syu proof; here the target dynamic loader
and libraries read the assembled database and packages on the build host.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.packaging import PacmanToolkit, ToolkitError
from distro_build.packaging.guest_test import prepare_upgrade_fixture, stage_upgrade_fixture, NAME, CONFIG
from distro_build.compose import runtime_packages

PROJECT = Path(__file__).resolve().parents[1]
STACK = runtime_packages(PROJECT, ["pacman"])
READY = (PROJECT / "out/native-toolkit/prefix/toolkit.json").is_file() and all(
    (PROJECT / f"out/state/packages/{name}.json").is_file() for name in STACK)


@unittest.skipUnless(READY, "source-built target package-manager stack not built")
class TargetPackageManagerTests(unittest.TestCase):
    def test_target_client_reads_native_database_and_upgrade_fixture(self):
        toolkit = PacmanToolkit(PROJECT / "out/native-toolkit/prefix")
        artifacts = [Path(json.loads((PROJECT / f"out/state/packages/{name}.json").read_text())["path"])
                     for name in STACK]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fixture = prepare_upgrade_fixture(base / "fixture", toolkit)
            self.assertEqual(prepare_upgrade_fixture(base / "fixture", toolkit), fixture)
            root = base / "root"
            toolkit.install(root, [*artifacts, Path(fixture["initial_package"])], bootstrap=True)
            staged = stage_upgrade_fixture(root, fixture, toolkit)
            self.assertEqual(staged["success_marker"], "CUSTOM_PACKAGE_UPGRADE_OK")
            keyring = root / "etc/pacman.d/gnupg"
            keyring.mkdir(parents=True)
            prefix = [str(root / "usr/lib/ld-linux-x86-64.so.2"), "--library-path", str(root / "usr/lib"),
                      str(root / "usr/bin/pacman"), "--config", str(root / "etc/pacman.conf"),
                      "--gpgdir", str(keyring),
                      "--root", str(root), "--dbpath", str(root / "var/lib/pacman")]
            environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
            version = subprocess.check_output([*prefix, "-Q", NAME], env=environment, text=True).strip()
            self.assertEqual(version, f"{NAME} 1.0-1")
            owner = subprocess.check_output([*prefix, "-Qoq", str(root / CONFIG)], env=environment, text=True).strip()
            self.assertEqual(owner, NAME)
            information = subprocess.check_output([*prefix, "-Qip", fixture["upgrade_package"]], env=environment, text=True)
            self.assertIn("1.0-2", information)
            self.assertEqual((root / CONFIG).read_text(), "administrator=preserved\n")
            repository_copy = root / "var/lib/custom-distro/repository" / Path(fixture["upgrade_package"]).name
            self.assertEqual(repository_copy.read_bytes(), Path(fixture["upgrade_package"]).read_bytes())
            with self.assertRaisesRegex(ToolkitError, "already exists"):
                stage_upgrade_fixture(root, fixture, toolkit)


if __name__ == "__main__":
    unittest.main()
