"""Regression checks for bootstrap cache artifacts, including tampering."""
import tempfile
from pathlib import Path
import unittest

from integrity import fingerprint, receipt, stage_artifacts


class IntegrityTests(unittest.TestCase):
    def test_binary_mutation_invalidates_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "cc1"
            binary.write_bytes(b"known source-built compiler")
            original = receipt([root])
            binary.write_bytes(b"substituted compiler")
            self.assertNotEqual(original, receipt([root]))

    def test_symlink_retargeting_and_added_files_are_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alias = root / "ld"
            alias.symlink_to("ld.bfd")
            original = receipt([root])
            alias.unlink()
            alias.symlink_to("untrusted-linker")
            self.assertNotEqual(original, receipt([root]))
            original = receipt([root])
            (root / "injected.so").write_bytes(b"payload")
            self.assertNotEqual(original, receipt([root]))

    def test_receipts_ignore_timestamp_changes(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tool"
            path.write_bytes(b"unchanged")
            original = receipt([path])
            os.utime(path, (1, 1))
            self.assertEqual(original, receipt([path]))

    def test_executable_mode_change_invalidates_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "compiler"
            path.write_bytes(b"same compiler bytes")
            path.chmod(0o755)
            original = receipt([path])
            path.chmod(0o644)
            self.assertNotEqual(original, receipt([path]))

    def test_kernel_headers_and_validation_do_not_capture_later_compiler_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / "tools"
            (root / "usr/include/linux").mkdir(parents=True)
            (root / "usr/include/linux/version.h").write_text("source-built API")
            marker = root / ".bootstrap-validated.json"
            marker.write_text("verified")
            kernel_paths = stage_artifacts("headers", [root / "usr/include/linux/version.h"], root, tools, "target")
            probe_paths = stage_artifacts("validate", [marker], root, tools, "target")
            kernel_receipt = receipt(kernel_paths)
            probe_receipt = receipt(probe_paths)
            (root / "usr/include/stdio.h").write_text("source-built glibc header")
            (tools / "pass2/bin").mkdir(parents=True)
            (tools / "pass2/bin/target-g++").write_text("later compiler")
            self.assertEqual(kernel_receipt, receipt(kernel_paths))
            self.assertEqual(probe_receipt, receipt(probe_paths))
            self.assertEqual(fingerprint(probe_receipt), fingerprint(receipt(probe_paths)))


if __name__ == "__main__":
    unittest.main()
