"""Verify private Meson staging and the native input report used by packages."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "bootstrap"))
from distro_build.model import BuildError
from distro_build.runner import seed_report
from integrity import fingerprint, receipt


class NativeMesonTests(unittest.TestCase):
    def stage(self, root):
        source = root / "source"
        module = source / "mesonbuild"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
        (module / "mesonmain.py").write_text("def main():\n    print('1.9.2')\n    return 0\n")
        (source / "COPYING").write_text("license fixture\n")
        tools = root / "out/bootstrap/root/tools"
        subprocess.run([sys.executable, "-B", str(PROJECT / "bootstrap/stages/meson-native.py")],
                       env=os.environ | {"CD_SOURCE_DIR": str(source), "CD_TOOLS": str(tools)},
                       check=True, capture_output=True)
        return tools / "native/bin/meson", tools / "native/lib/meson"

    def test_private_tool_runs_without_changing_receipt_and_reports_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper, library = self.stage(root)
            before = receipt([wrapper, library])
            stamp = root / "out/bootstrap/work/stamps/meson-native.json"
            stamp.parent.mkdir(parents=True)
            source_pin = {"meson": {"version": "1.9.2", "sha256": "a" * 64}}
            record = {"fingerprint": "stage-fixture", "artifact_fingerprint": fingerprint(before),
                      "identity": {"sources": source_pin}}
            stamp.write_text(json.dumps(record))
            for _ in range(2):
                result = subprocess.run([str(wrapper), "--version"], check=True,
                                        capture_output=True, text=True)
                self.assertEqual(result.stdout.strip(), "1.9.2")
            report = seed_report(root)
            self.assertEqual(report["tools"]["meson"]["path"], str(wrapper))
            self.assertEqual(report["tools"]["meson"]["version"], "1.9.2")
            self.assertEqual(report["bootstrap_tools"]["meson"]["sources"], source_pin)
            self.assertEqual(receipt([wrapper, library]), before)
            self.assertFalse(list(library.rglob("__pycache__")))
            (library / "mesonbuild/mesonmain.py").write_text("raise SystemExit('changed module')\n")
            self.assertNotEqual(fingerprint(receipt([wrapper, library])), record["artifact_fingerprint"])

    def test_private_tool_requires_bootstrap_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root)
            with self.assertRaisesRegex(BuildError, "lacks a bootstrap receipt"):
                seed_report(root)


if __name__ == "__main__":
    unittest.main()
