"""Development stage reuse must observe edits and never adopt failed work."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.cli import DevelopmentCache
from distro_build.model import BuildError


class DevelopmentCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.recipe = self.source / "recipe.py"
        self.recipe.write_text("original")
        self.artifact = self.root / "package.tar.xz"
        self.cache = DevelopmentCache(self.root, "systemd")
        self.action = Mock(side_effect=self.build)
        self.addCleanup(patch.stopall)
        patch("distro_build.cli.emit").start()

    def build(self):
        self.artifact.write_text(self.recipe.read_text())
        return {"artifact": str(self.artifact), "inputs": self.recipe.read_text()}

    def run_stage(self):
        return self.cache.run("packages", lambda: [self.source], lambda _: [self.artifact], self.action)

    def test_unchanged_stage_reuses_its_actual_result(self):
        first = self.run_stage()
        self.assertEqual(self.run_stage(), first)
        self.action.assert_called_once()

    def test_same_size_edit_with_restored_mtime_invalidates_cache(self):
        self.run_stage()
        timestamp = self.recipe.stat()
        self.recipe.write_text("modified")
        os.utime(self.recipe, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
        self.assertEqual(self.run_stage()["inputs"], "modified")
        self.assertEqual(self.action.call_count, 2)

    def test_changed_or_missing_output_is_rebuilt(self):
        self.run_stage()
        self.artifact.write_text("tampered")
        self.run_stage()
        self.artifact.unlink()
        self.run_stage()
        self.assertEqual(self.artifact.read_text(), "original")
        self.assertEqual(self.action.call_count, 3)

    def test_added_removed_and_mode_changed_inputs_invalidate_cache(self):
        self.run_stage()
        added = self.source / "patch"
        added.touch()
        self.run_stage()
        added.unlink()
        self.run_stage()
        self.recipe.chmod(0o755)
        self.run_stage()
        self.assertEqual(self.action.call_count, 4)

    def test_changed_symlink_target_is_observed(self):
        external = self.root / "external"
        external.write_text("one")
        (self.source / "linked-input").symlink_to(external)
        self.run_stage()
        external.write_text("two")
        self.run_stage()
        self.assertEqual(self.action.call_count, 2)

    def test_failed_verification_removes_old_success(self):
        self.run_stage()
        self.cache.verify = True
        self.action.side_effect = RuntimeError("failed verification")
        with self.assertRaisesRegex(RuntimeError, "failed verification"):
            self.run_stage()
        self.assertFalse((self.cache.directory / "packages.json").exists())
        self.cache.verify = False
        self.action.side_effect = self.build
        self.run_stage()
        self.assertEqual(self.action.call_count, 3)

    def test_input_edit_during_build_prevents_success_receipt(self):
        def edit_during_build():
            result = self.build()
            self.recipe.write_text("changed while building")
            return result
        self.action.side_effect = edit_during_build
        with self.assertRaisesRegex(BuildError, "inputs changed during preparation"):
            self.run_stage()
        self.assertFalse((self.cache.directory / "packages.json").exists())

    def test_verify_always_runs_the_action(self):
        self.run_stage()
        self.cache.verify = True
        self.run_stage()
        self.assertEqual(self.action.call_count, 2)

    def test_corrupt_receipts_are_replaced_after_a_successful_action(self):
        self.run_stage()
        record = self.cache.directory / "packages.json"
        for text in ("{broken", "[]"):
            record.write_text(text)
            self.run_stage()
        self.assertEqual(self.action.call_count, 3)

    def test_source_change_does_not_rebuild_unrelated_essentials(self):
        self.run_stage()
        policy = self.root / "policy"
        policy.write_text("policy one")
        image = self.root / "disk.img"
        compose = Mock(side_effect=lambda: image.write_text(policy.read_text()))
        def image_stage():
            return self.cache.run("image", lambda: [policy, self.artifact], lambda _: [image], compose)
        image_stage()
        policy.write_text("policy two")
        self.run_stage()
        image_stage()
        self.action.assert_called_once()
        self.assertEqual(compose.call_count, 2)
        self.assertEqual(image.read_text(), "policy two")

    def test_environment_change_invalidates_without_recording_values(self):
        with patch.dict(os.environ, {"CFLAGS": "private-build-option"}):
            self.run_stage()
        record = (self.cache.directory / "packages.json").read_text()
        self.assertNotIn("private-build-option", record)
        self.run_stage()
        self.assertEqual(self.action.call_count, 2)


if __name__ == "__main__":
    unittest.main()
