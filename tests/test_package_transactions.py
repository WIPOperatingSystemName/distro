"""Integration against the source-built libalpm toolkit, never mock installs."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.packaging import PacmanToolkit, ToolkitError, export_package

PREFIX = Path(os.environ.get("DISTRO_TEST_TOOLKIT", str(Path(__file__).resolve().parents[1] / "out/native-toolkit/prefix")))


@unittest.skipUnless((PREFIX / "toolkit.json").is_file(), "source-built native toolkit not built")
class TransactionTests(unittest.TestCase):
    def test_real_install_upgrade_backup_ownership_and_remove(self):
        toolkit = PacmanToolkit(PREFIX)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage, root, packages = base / "stage", base / "root", base / "packages"
            (stage / "etc").mkdir(parents=True)
            (stage / "usr/share/fixture").mkdir(parents=True)
            (stage / "etc/fixture.conf").write_text("default=old\n")
            (stage / "usr/share/fixture/data").write_text("first\n")
            meta = {"name": "transaction-fixture", "version": "1.0", "revision": 1,
                    "arch": "any", "backup": ["etc/fixture.conf"]}
            first = export_package(stage, packages, meta, source_date_epoch=1234567890)
            self.assertIn("transaction-fixture", toolkit.inspect_native(first.path))
            toolkit.install(root, [first.path], bootstrap=True)
            self.assertIn("transaction-fixture 1.0-1", toolkit.query(root))
            self.assertIn("transaction-fixture", toolkit.query(root, "--owns", str(root / "etc/fixture.conf")))
            (root / "etc/fixture.conf").write_text("administrator=custom\n")
            (stage / "etc/fixture.conf").write_text("default=new\n")
            (stage / "usr/share/fixture/data").write_text("second\n")
            meta["revision"] = 2
            second = export_package(stage, packages, meta, source_date_epoch=1234567890)
            toolkit.install(root, [second.path], bootstrap=True)
            self.assertEqual((root / "etc/fixture.conf").read_text(), "administrator=custom\n")
            self.assertEqual((root / "etc/fixture.conf.pacnew").read_text(), "default=new\n")
            self.assertEqual((root / "usr/share/fixture/data").read_text(), "second\n")
            self.assertIn("transaction-fixture 1.0-2", toolkit.query(root))
            toolkit.remove(root, ["transaction-fixture"])
            self.assertFalse((root / "usr/share/fixture/data").exists())
            self.assertEqual((root / "etc/fixture.conf.pacsave").read_text(), "administrator=custom\n")

    def test_native_dependency_check_rejects_missing_runtime(self):
        toolkit = PacmanToolkit(PREFIX)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / "stage"
            stage.mkdir()
            artifact = export_package(stage, base / "packages",
                                      {"name": "missing-dep", "version": "1.0", "arch": "any",
                                       "depends": ["required-runtime>=1.0"]}, source_date_epoch=123)
            with self.assertRaisesRegex(ToolkitError, "dependency"):
                toolkit.install(base / "root", [artifact.path], bootstrap=True)

    def test_compile_sysroot_omits_runtime_closure_but_preserves_metadata_and_conflicts(self):
        toolkit = PacmanToolkit(PREFIX)
        project_work = PREFIX.resolve().parent.parent / "work"
        project_work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="sdk-transaction-", dir=project_work) as temp:
            base = Path(temp)
            root, stage = base / "sysroot", base / "stage"
            (stage / "usr/include").mkdir(parents=True)
            (stage / "usr/include/sdk-fixture.h").write_text("/* target SDK header */\n")
            script = base / "script.install"
            script.write_text("pre_install() { exit 77; }\n")
            artifact = export_package(stage, base / "packages",
                                      {"name": "sdk-fixture", "version": "1.0", "arch": "any",
                                       "depends": ["runtime-only-service>=1.0"],
                                       "conflicts": ["conflicting-sdk-fixture"]},
                                      source_date_epoch=123, install_script=script)
            with self.assertRaisesRegex(ToolkitError, "dependency"):
                toolkit.install(base / "image-root", [artifact.path], bootstrap=True)
            toolkit.install(root, [artifact.path], bootstrap=True, compile_sysroot=True,
                            expected_hashes={artifact.path.name: artifact.sha256})
            self.assertTrue((root / "usr/include/sdk-fixture.h").is_file())
            self.assertIn("runtime-only-service>=1.0", (root / "var/lib/pacman/local/sdk-fixture-1.0-1/desc").read_text())
            other_stage = base / "other-stage"
            other_stage.mkdir()
            other = export_package(other_stage, base / "packages",
                                   {"name": "conflicting-sdk-fixture", "version": "1.0", "arch": "any"},
                                   source_date_epoch=123)
            with self.assertRaisesRegex(ToolkitError, "conflict"):
                toolkit.install(root, [other.path], bootstrap=True, compile_sysroot=True)
            self.assertNotIn("conflicting-sdk-fixture 1.0-1", toolkit.query(root))

    def test_compile_sysroot_mode_rejected_outside_private_work_by_api_and_native_helper(self):
        toolkit = PacmanToolkit(PREFIX)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / "stage"
            stage.mkdir()
            artifact = export_package(stage, base / "packages",
                                      {"name": "scope-fixture", "version": "1.0", "arch": "any"},
                                      source_date_epoch=123)
            root = base / "sysroot"
            with self.assertRaisesRegex(ToolkitError, "generated out/work"):
                toolkit.install(root, [artifact.path], bootstrap=True, compile_sysroot=True)
            self.assertFalse(root.exists())
            database = root / "var/lib/pacman"
            database.mkdir(parents=True)
            result = subprocess.run([str(PREFIX / "bin/distro-seed-install"), "--compile-sysroot",
                                     str(root), str(database), "x86_64", str(artifact.path)],
                                    env=toolkit._env(), text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("generated project out/work", result.stderr)
            work = PREFIX.resolve().parent.parent / "work"
            work.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="sdk-scope-", dir=work) as scoped:
                escape = Path(scoped) / "escape"
                escape.symlink_to(base, target_is_directory=True)
                with self.assertRaisesRegex(ToolkitError, "generated out/work"):
                    toolkit.install(escape / "sysroot", [artifact.path], bootstrap=True, compile_sysroot=True)
                inside = Path(scoped) / "sysroot"
                inside.mkdir()
                result = subprocess.run([str(PREFIX / "bin/distro-seed-install"), "--compile-sysroot",
                                         str(inside), str(database), "x86_64", str(artifact.path)],
                                        env=toolkit._env(), text=True, capture_output=True)
                self.assertEqual(result.returncode, 2, "native helper must also reject a database outside the private sysroot")

    def test_snapshot_digest_is_checked_before_transaction(self):
        toolkit = PacmanToolkit(PREFIX)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / "stage"
            stage.mkdir()
            artifact = export_package(stage, base / "packages", {"name": "fixture", "version": "1.0", "arch": "any"}, source_date_epoch=123)
            with self.assertRaisesRegex(ToolkitError, "digest differs"):
                toolkit.install(base / "root", [artifact.path], bootstrap=True,
                                expected_hashes={artifact.path.name: "0" * 64})

    def test_standard_repo_add_database_contains_exported_package(self):
        import tarfile
        toolkit = PacmanToolkit(PREFIX)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / "stage"
            stage.mkdir()
            artifact = export_package(stage, base / "packages", {"name": "repo-fixture", "version": "1.0", "arch": "any"}, source_date_epoch=123)
            repository = toolkit.create_repository(base / "repository", [artifact.path])
            with tarfile.open(repository["database"]) as database:
                desc = database.extractfile("repo-fixture-1.0-1/desc").read().decode()
            self.assertIn("%NAME%\nrepo-fixture\n", desc)
            self.assertIn(artifact.sha256, desc)

    def test_seed_install_does_not_execute_package_setup(self):
        toolkit = PacmanToolkit(PREFIX)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / "stage"
            (stage / "usr/share/libalpm/hooks").mkdir(parents=True)
            # A failed pre-hook would abort the transaction if hooks ran.
            (stage / "usr/share/libalpm/hooks/00-failure.hook").write_text(
                "[Trigger]\nOperation = Install\nType = Package\nTarget = *\n"
                "[Action]\nWhen = PreTransaction\nExec = /this-command-must-never-run\nAbortOnFail\n")
            script = base / "script.install"
            script.write_text("pre_install() { exit 77; }\n")
            artifact = export_package(stage, base / "packages", {"name": "setup-fixture", "version": "1.0", "arch": "any"}, source_date_epoch=123, install_script=script)
            toolkit.install(base / "root", [artifact.path], bootstrap=True)
            toolkit.install(base / "root", [artifact.path], bootstrap=True)
            self.assertIn("setup-fixture", toolkit.query(base / "root"))


if __name__ == "__main__":
    unittest.main()
