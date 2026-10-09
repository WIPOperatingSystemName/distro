"""Cargo synchronization must preserve unchanged inputs and detect deletions."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.incremental import synchronize


class IncrementalTests(unittest.TestCase):
    def test_unchanged_files_keep_mtimes_but_restored_older_edits_are_touched(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            target = Path(temporary) / "workspace"
            source.mkdir()
            file = source / "main.rs"
            file.write_text("old")
            synchronize(source, target)
            copied = target / file.name
            original = copied.stat().st_mtime_ns
            synchronize(source, target)
            self.assertEqual(copied.stat().st_mtime_ns, original)
            file.write_text("new")
            os.utime(file, (1, 1))
            synchronize(source, target)
            self.assertEqual(copied.read_text(), "new")
            self.assertGreater(copied.stat().st_mtime, 1)

    def test_deleted_modules_and_changed_file_types_are_not_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            target = Path(temporary) / "workspace"
            source.mkdir()
            (source / "removed.rs").write_text("old")
            (source / "module").mkdir()
            synchronize(source, target)
            (source / "removed.rs").unlink()
            (source / "module").rmdir()
            (source / "module").write_text("file")
            synchronize(source, target)
            self.assertFalse((target / "removed.rs").exists())
            self.assertEqual((target / "module").read_text(), "file")


if __name__ == "__main__":
    unittest.main()
