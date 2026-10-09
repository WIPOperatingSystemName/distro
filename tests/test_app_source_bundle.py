from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.apps import INPUTS, prepare, _shell_overlays, _software_capture_supported
from distro_build.model import BuildError
from distro_build.source_bundle import BUNDLE_INPUTS, export_bundle, import_bundle


class AppBundleTests(unittest.TestCase):
    def test_software_capture_is_opted_in_by_the_recorded_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            manifest = workspace / "telorgon/crates/telorgon/Cargo.toml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("[package.metadata.telorgon]\nsoftware-screencast = true\n")
            shell = workspace / "test-shell/src/main.rs"
            shell.parent.mkdir(parents=True)
            shell.write_text(
                '    if let Err(error) = desktop_entry::ensure_settings_entry() {\n'
                '        eprintln!("Could not create the Settings launcher entry: {error}");\n'
                '    }\n.capture(Capture::desktop())\n.renderer(Renderer::Vulkan)\n')
            self.assertTrue(_software_capture_supported(workspace))
            _shell_overlays(workspace)
            self.assertIn(".capture(Capture::desktop())", shell.read_text())
            self.assertIn(".renderer(Renderer::Auto)", shell.read_text())
            self.assertNotIn("ensure_settings_entry", shell.read_text())

    def test_prepare_preserves_bundle_origin_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "custom-distro"
            project.mkdir()
            source_root = project / "sources"
            source_root.mkdir()
            for name in BUNDLE_INPUTS:
                source = source_root / name
                source.mkdir()
                (source / "Cargo.toml").write_text('[package]\nname="fixture"\nversion="0.1.0"\n')
                (source / "Cargo.lock").write_text("version = 4\n")
                code = source / "src"
                code.mkdir()
                (code / "main.rs").write_text("fn main() {}\n")
            (source_root / "test-shell/src/main.rs").write_text(
                '    if let Err(error) = desktop_entry::ensure_settings_entry() {\n'
                '        eprintln!("Could not create the Settings launcher entry: {error}");\n'
                '    }\n.capture(Capture::desktop())\n.renderer(Renderer::Vulkan)\n')
            self.assertNotIn("shell-settings", BUNDLE_INPUTS)
            self.assertFalse((root / "shell-settings").exists())
            exported = export_bundle(project)
            imported = import_bundle(project, Path(exported["archive"]), exported["sha256"])
            workspace = Path(imported["root"])
            self.assertFalse((workspace / "shell-settings").exists())
            prepared = prepare(project, source_root=workspace)
            self.assertEqual(set(prepared["sources"]), set(INPUTS))
            self.assertNotIn("shell-settings", prepared["sources"])
            self.assertNotIn("shell-settings", prepared["cargo_locks"])
            self.assertFalse((Path(prepared["workspace"]) / "shell-settings").exists())
            shell = Path(prepared["workspace"]) / "test-shell/src/main.rs"
            self.assertIn(".capture(Capture::new())", shell.read_text())
            self.assertEqual(prepared["origin"]["kind"], "verified-portable-source-bundle")
            self.assertEqual(prepared["origin"]["identity"], imported["identity"])
            self.assertEqual(prepared["sources"]["telorgon"]["origin"]["kind"], prepared["origin"]["kind"])
            (workspace / "telorgon/Cargo.lock").write_text("changed\n")
            with self.assertRaisesRegex(BuildError, "differs"):
                prepare(project, source_root=workspace)


if __name__ == "__main__":
    unittest.main()
