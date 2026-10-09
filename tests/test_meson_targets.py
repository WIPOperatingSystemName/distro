"""Package builds must not pull disabled tests through Meson test prerequisites."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.desktop_native import DesktopBuild


@unittest.skipUnless(all(shutil.which(tool) for tool in ("meson", "ninja", "gcc")),
                     "native fixture requires Meson, Ninja and GCC")
class MesonTargetTests(unittest.TestCase):
    def test_installed_payload_build_avoids_broken_disabled_test(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "payload.c").write_text("int payload(void) { return 42; }\n")
            (source / "broken.c").write_text(
                "extern void test_setup_logging(void);\n"
                "int main(void) { test_setup_logging(); return 0; }\n")
            (source / "meson.build").write_text("""project('fixture', 'c')
shared_library('payload', 'payload.c', install: true)
broken = executable('disabled-test', 'broken.c', build_by_default: false, install: false)
test('symbol-audit', find_program('true'), depends: broken)
custom_target('generated-data', output: 'generated.txt',
  command: [find_program('python3'), '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("generated payload")', '@OUTPUT@'],
  install: true, install_dir: get_option('datadir') / 'fixture', build_by_default: false)
""")
            build = DesktopBuild.__new__(DesktopBuild)
            build.source = source
            build.work = root / "work"
            build.work.mkdir()
            build.stage = root / "stage"
            build.sysroot = root / "sysroot"
            build.sysroot.mkdir()
            build.tools = root / "tools"
            build.wrapper = Path(shutil.which("gcc"))
            build.target = "x86_64-fixture-linux-gnu"
            build.jobs = "2"
            build.env = dict(os.environ, AR="/usr/bin/ar", STRIP="/usr/bin/strip")
            build.native_file = build.work / "native.ini"
            build.native_file.write_text("[binaries]\nc = '/usr/bin/gcc'\n")

            def run(arguments, cwd=None):
                subprocess.run(arguments, cwd=cwd or source, env=build.env,
                               capture_output=True, text=True, check=True)

            build.run = run
            build.meson([], installed_only=True)
            self.assertTrue((build.stage / "usr/lib/libpayload.so").is_file())
            self.assertEqual((build.stage / "usr/share/fixture/generated.txt").read_text(),
                             "generated payload")
            self.assertFalse((build.work / "target-build/disabled-test").exists())
            self.assertFalse((build.stage / "usr/bin/disabled-test").exists())

            # The same project reproduces the default-target failure observed
            # in systemd when its symbol audit depends on disabled tests.
            default = subprocess.run(["ninja", "-C", str(build.work / "target-build")],
                                     env=build.env, capture_output=True, text=True)
            self.assertNotEqual(default.returncode, 0)
            self.assertIn("test_setup_logging", default.stdout + default.stderr)


if __name__ == "__main__":
    unittest.main()
