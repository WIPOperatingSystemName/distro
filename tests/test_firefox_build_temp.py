"""Exercise the Manager socket failure that stopped Firefox's IPDL export."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "firefox_build_temp", Path(__file__).resolve().parents[1] / "packages/firefox/build_temp.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.desktop_native import _link_wrapper

MANAGER = """from multiprocessing import Manager
import json
with Manager() as manager:
    values = manager.dict()
    values['ipc'] = 'working'
    print(json.dumps({'value': values['ipc'], 'socket': manager.address}))
"""


@unittest.skipUnless(sys.platform == "linux", "Linux UNIX socket path limit")
class FirefoxBuildTempTests(unittest.TestCase):
    def test_hashed_work_path_fails_and_short_environment_completes_real_ipc(self):
        with tempfile.TemporaryDirectory(prefix="fx-test-") as directory:
            output = Path(directory) / "out"
            deep = output / "work/firefox" / ("a" * 64) / "tmp"
            deep.mkdir(parents=True)
            environment = {**os.environ, "TMPDIR": str(deep)}
            failed = subprocess.run([sys.executable, "-c", MANAGER], env=environment,
                                    capture_output=True, text=True, timeout=15)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("AF_UNIX path too long", failed.stderr)
            with helper.temporary_environment(environment, output) as short:
                scratch = Path(short["TMPDIR"])
                self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)
                passed = subprocess.run([sys.executable, "-c", MANAGER], env=short,
                                        capture_output=True, text=True, check=True, timeout=15)
                result = json.loads(passed.stdout)
                self.assertEqual(result["value"], "working")
                self.assertTrue(Path(result["socket"]).is_relative_to(scratch))
            self.assertFalse(scratch.exists())
            self.assertEqual(environment["TMPDIR"], str(deep))

    def test_concurrent_builds_have_private_directories_and_failure_cleans_up(self):
        with tempfile.TemporaryDirectory(prefix="fx-test-") as directory:
            output = Path(directory) / "out"
            with helper.temporary_environment({}, output) as first:
                with self.assertRaisesRegex(RuntimeError, "build failed"):
                    with helper.temporary_environment({}, output) as second:
                        scratch = Path(second["TMPDIR"])
                        self.assertNotEqual(first["TMPDIR"], second["TMPDIR"])
                        (scratch / "temporary-file").write_text("temporary")
                        raise RuntimeError("build failed")
                self.assertFalse(scratch.exists())
                self.assertTrue(Path(first["TMPDIR"]).exists())

    def test_overlong_output_root_fails_with_an_actionable_error(self):
        with tempfile.TemporaryDirectory(prefix="fx-test-") as directory:
            output = Path(directory) / ("long-checkout-" * 6) / "out"
            with self.assertRaisesRegex(RuntimeError, "shorter checkout path"):
                with helper.temporary_environment({}, output):
                    self.fail("Overlong socket path was accepted")
            self.assertEqual(list((output / "tmp").iterdir()), [])

    @unittest.skipUnless(shutil.which("gcc"), "Declared host compiler seed")
    def test_configure_can_compile_private_temporary_objects_and_other_paths_stay_denied(self):
        with tempfile.TemporaryDirectory(prefix="fx-test-") as directory:
            output = Path(directory) / "out"
            work = output / "work"
            work.mkdir(parents=True)
            wrapper = work / "target-cc"
            with helper.temporary_environment(dict(os.environ), output) as environment:
                scratch = Path(environment["TMPDIR"])
                source = scratch / "conftest.c"
                source.write_text("int main(void) { return 0; }\n")
                target = scratch / "conftest.o"
                _link_wrapper(wrapper, Path(shutil.which("gcc")), work / "sysroot", [work, scratch])
                subprocess.run([str(wrapper), "-c", str(source), "-o", str(target)], check=True,
                               capture_output=True, text=True, timeout=15)
                self.assertEqual(target.read_bytes()[:4], b"\x7fELF")
                refused = subprocess.run([str(wrapper), "-c", str(source), "-o", str(output / "outside.o")],
                                         capture_output=True, text=True, timeout=15)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("refused a host include/library path", refused.stderr)


if __name__ == "__main__":
    unittest.main()
