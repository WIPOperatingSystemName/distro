"""Exercise packaging of the empty journal directory with native install ACLs."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("systemd_recipe", PROJECT / "packages/systemd/build.py")
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


class JournalStageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("setfacl"), "native ACL installation tool unavailable")
    def test_native_acl_removed_without_removing_journal_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            stage = work / "stage"
            journal = stage / "var/log/journal"
            journal.mkdir(parents=True, mode=0o755)
            group = os.getgid()
            subprocess.run(["setfacl", "-nm", f"g:{group}:rx,d:g:{group}:rx", str(journal)], check=True)
            self.assertTrue(os.listxattr(journal))
            recipe.normalize_journal_directory(stage, work)
            self.assertTrue(journal.is_dir())
            self.assertFalse(os.listxattr(journal))
            self.assertEqual(journal.stat().st_mode & 0o7777, 0o755)
            report = json.loads((work / "journal-directory-policy.json").read_text())
            self.assertIn("system.posix_acl_default", report["removed_native_install_attributes"])

    def test_nonempty_journal_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            journal = work / "stage/var/log/journal"
            journal.mkdir(parents=True)
            (journal / "unexpected.log").write_text("must preserve\n")
            with self.assertRaisesRegex(RuntimeError, "empty staged"):
                recipe.normalize_journal_directory(work / "stage", work)
            self.assertEqual((journal / "unexpected.log").read_text(), "must preserve\n")


if __name__ == "__main__":
    unittest.main()
