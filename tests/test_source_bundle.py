from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.model import BuildError
from distro_build.source_bundle import BUNDLE_INPUTS, export_bundle, import_bundle, verify_workspace


class SourceBundleTests(unittest.TestCase):
    def fixture(self, root):
        project = root / "custom-distro"
        project.mkdir()
        source_root = project / "sources"
        source_root.mkdir()
        for name in BUNDLE_INPUTS:
            source = source_root / name
            source.mkdir()
            (source / "Cargo.toml").write_text('[package]\nname="fixture"\nversion="0.1.0"\n')
            (source / "Cargo.lock").write_text("version = 4\n")
            code = source / ("crates" if name in {"telorgon", "telorgon-bootloader"} else "src")
            code.mkdir()
            (code / "main.rs").write_text("fn main() {}\n")
        return project

    def test_portable_inputs_reproduce_and_reject_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.fixture(Path(temporary))
            self.assertNotIn("shell-settings", BUNDLE_INPUTS)
            self.assertFalse((project.parent / "shell-settings").exists())
            first = export_bundle(project)
            self.assertEqual(set(first["manifest"]["sources"]), set(BUNDLE_INPUTS))
            self.assertNotIn("shell-settings", first["manifest"]["sources"])
            second = export_bundle(project)
            self.assertEqual(first["sha256"], second["sha256"])
            result = import_bundle(project, Path(first["archive"]), first["sha256"])
            imported = Path(result["root"])
            self.assertFalse((imported / "shell-settings").exists())
            self.assertEqual(verify_workspace(imported)["identity"], first["identity"])
            (imported / "telorgon/Cargo.lock").write_text("changed\n")
            with self.assertRaisesRegex(BuildError, "differs"):
                import_bundle(project, Path(first["archive"]), first["sha256"])

    def test_expected_digest_and_unrecorded_inputs_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.fixture(Path(temporary))
            exported = export_bundle(project)
            with self.assertRaisesRegex(BuildError, "SHA256"):
                import_bundle(project, Path(exported["archive"]), "0" * 64)
            result = import_bundle(project, Path(exported["archive"]), exported["sha256"])
            (Path(result["root"]) / "telorgon/extra.rs").write_text("unrecorded\n")
            with self.assertRaisesRegex(BuildError, "unrecorded"):
                verify_workspace(Path(result["root"]))


if __name__ == "__main__":
    unittest.main()
