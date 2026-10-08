"""Bootstrap cache gates must cover changed artifacts and prerequisites."""
from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap"
sys.path.insert(0, str(BOOTSTRAP))
from integrity import fingerprint, receipt

SPEC = importlib.util.spec_from_file_location("bootstrap_runner", BOOTSTRAP / "run-stage.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class BootstrapCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stamps = self.root / "stamps"
        self.stamps.mkdir()
        self.stage = {"name": "compiler", "script": "compiler.sh", "requires": [],
                      "sources": [], "outputs": ["compiler"]}
        (self.root / "compiler.sh").write_text("build the declared target compiler")
        self.binary = self.root / "compiler"
        self.binary.write_bytes(b"verified compiler")
        self.patch = patch.object(runner, "HERE", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.engine = {"runner": "recorded runner"}
        self.seeds = {"compiler": "recorded native seed"}
        self.identity = runner.stage_identity(self.stage, "target", self.root, self.root / "tools",
                                             [], {}, self.engine, self.seeds)
        self.artifacts = receipt([self.binary])
        self.record = {"schema": 2, "identity": self.identity, "fingerprint": fingerprint(self.identity),
                       "artifact_roots": [str(self.binary)], "artifacts": self.artifacts,
                       "artifact_fingerprint": fingerprint(self.artifacts)}
        (self.stamps / "compiler.json").write_text(json.dumps(self.record))

    def check(self, engine=None, seeds=None):
        return runner.check_prerequisites("compiler", {"compiler": self.stage}, self.stamps, "target",
                                          self.root, self.root / "tools", {}, engine or self.engine,
                                          seeds or self.seeds, {})

    def test_unchanged_receipts_allow_cache_reuse(self):
        self.assertEqual(self.check()["fingerprint"], self.record["fingerprint"])

    def test_changed_binary_is_rejected(self):
        self.binary.write_bytes(b"unexpected compiler")
        with self.assertRaisesRegex(ValueError, "artifacts changed"):
            self.check()

    def test_changed_seed_or_runner_is_rejected_without_relabeling_artifacts(self):
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.check(seeds={"compiler": "different seed"})
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.check(engine={"runner": "different runner"})
        self.assertEqual(self.binary.read_bytes(), b"verified compiler")

    def test_changed_prerequisite_script_is_rejected(self):
        (self.root / "compiler.sh").write_text("different compiler build")
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.check()

    def test_legacy_stamp_requires_explicit_migration(self):
        with self.assertRaisesRegex(ValueError, "--upgrade-stamps"):
            runner.check_stamp({"fingerprint": "legacy marker"}, "compiler")

    def test_read_only_output_validation_does_not_create_missing_directories(self):
        missing = self.root / "missing-output"
        with patch.object(runner, "PROJECT", self.root):
            with self.assertRaisesRegex(ValueError, "Missing work directory"):
                runner.project_output(str(missing), "work", create=False)
        self.assertFalse(missing.exists())

    def test_checker_leaves_completion_records_unchanged(self):
        path = self.stamps / "compiler.json"
        original = path.read_bytes()
        self.check()
        self.assertEqual(original, path.read_bytes())


def load_tests(loader, standard_tests, pattern):
    spec = importlib.util.spec_from_file_location("bootstrap_artifact_tests", BOOTSTRAP / "test_integrity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    standard_tests.addTests(loader.loadTestsFromModule(module))
    return standard_tests
