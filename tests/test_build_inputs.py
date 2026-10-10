"""Exercise mutable contributor inputs and concurrent real package exports."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.model import Recipe
from distro_build.runner import build_package
from distro_build.cli import reuse_package_set
from distro_build import cli


class BuildInputsTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "source"
        source.mkdir()
        (source / "README").write_text("source fixture\n")
        cache = root / "out/sources/downloads"
        cache.mkdir(parents=True)
        archive = cache / "example.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(source, arcname="example", recursive=True)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        engine = root / "src/distro_build"
        engine.mkdir(parents=True)
        (engine / "__init__.py").write_text("")
        recipe = root / "packages/example"
        recipe.mkdir(parents=True)
        (recipe / "package.toml").write_text(f'''schema = 1
[package]
name = "example"
version = "1.0"
revision = 1
arch = "any"
description = "Build isolation regression fixture"
license = "MIT"
[[sources]]
name = "main"
url = "https://example.invalid/example.tar.gz"
filename = "example.tar.gz"
sha256 = "{digest}"
[build]
adapter = "script"
commands = [["python3", "{{recipe}}/build.py"]]
''')
        script = '''from pathlib import Path
import os,time
work=Path(os.environ['CD_WORK_DIR'])
(work/'started').touch()
with (work.parents[2]/'calls').open('a') as stream: stream.write('build\\n')
time.sleep(0.4)
stage=Path(os.environ['CD_STAGE_DIR'])
(stage/'payload').write_text(Path(__file__).read_text())
# ORIGINAL_INPUT
'''
        (recipe / "build.py").write_text(script)
        return Recipe.load(recipe / "package.toml"), script

    def test_recipe_snapshot_survives_contributor_edit_during_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe, original = self.fixture(root)
            with patch("distro_build.runner.seed_report", return_value={"kind": "test-seed"}), ThreadPoolExecutor(1) as pool:
                future = pool.submit(build_package, root, recipe, jobs=1, seed_build=True)
                deadline = time.monotonic() + 5
                while not list((root / "out/work/example").glob("*/started")):
                    if time.monotonic() > deadline:
                        self.fail("fixture build did not start")
                    time.sleep(0.01)
                (recipe.path.parent / "build.py").write_text("raise SystemExit('edited input')\n")
                result = future.result(timeout=5)
            self.assertEqual((Path(result["stage"]) / "payload").read_text(), original)
            inputs = Path(result["provenance"]["frozen_inputs"])
            self.assertEqual((inputs / "packages/example/build.py").read_text(), original)

    def test_concurrent_same_package_builds_share_one_verified_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe, _ = self.fixture(root)
            with patch("distro_build.runner.seed_report", return_value={"kind": "test-seed"}), ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(build_package, root, recipe, jobs=1, seed_build=True) for _ in range(2)]
                results = [future.result(timeout=10) for future in futures]
            self.assertEqual(results[0]["sha256"], results[1]["sha256"])
            self.assertEqual((root / "out/calls").read_text(), "build\n")
            state = json.loads((root / "out/state/packages/example.json").read_text())
            self.assertEqual(state["sha256"], results[0]["sha256"])

    def test_run_reuses_verified_packages_but_rejects_changed_inputs_or_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe, original = self.fixture(root)
            compiler = root / "out/bootstrap/root/tools/bin/x86_64-custom-linux-gnu-gcc"
            compiler.parent.mkdir(parents=True)
            compiler.touch()
            seed = {"kind": "test-seed"}
            with patch("distro_build.runner.seed_report", return_value=seed), patch("distro_build.runner.check_bootstrap") as audit:
                result = build_package(root, recipe, jobs=1)
                self.assertTrue(reuse_package_set(root, {recipe.name: recipe}, [recipe]))
                other = root / "packages/consumer"
                other.mkdir()
                (other / "package.toml").write_text(recipe.path.read_text().replace('name = "example"', 'name = "consumer"'))
                (other / "build.py").write_text(original)
                build_package(root, Recipe.load(other / "package.toml"), jobs=1)
                audit.reset_mock()
                self.assertEqual(cli.main(["--project", str(root), "build", "example", "consumer"], quiet=True), 0)
                audit.assert_called_once()
                (recipe.path.parent / "build.py").write_text(original + "# changed recipe\n")
                self.assertFalse(reuse_package_set(root, {recipe.name: recipe}, [recipe]))
                (recipe.path.parent / "build.py").write_text(original)
                archive = Path(result["path"])
                saved = archive.read_bytes()
                archive.write_bytes(b"tampered archive")
                self.assertFalse(reuse_package_set(root, {recipe.name: recipe}, [recipe]))
                archive.write_bytes(saved)
                with patch("distro_build.runner.seed_report", return_value={"kind": "changed-seed"}):
                    self.assertFalse(reuse_package_set(root, {recipe.name: recipe}, [recipe]))


if __name__ == "__main__":
    unittest.main()
