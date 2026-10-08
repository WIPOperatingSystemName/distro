"""Manifest declarations cannot satisfy a runtime qualification assertion."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.vm import observed_marker, observed_success, success_markers


class MarkerTests(unittest.TestCase):
    def test_requires_an_actual_completed_guest_line(self):
        marker = "CUSTOM_SIGNED_PACKAGE_UPGRADE_OK"
        manifest = '{"success_marker": "' + marker + '"}\r\n'
        self.assertFalse(observed_marker(manifest, marker))
        self.assertFalse(observed_marker(manifest + "attempting " + marker + "\n", marker))
        self.assertFalse(observed_marker(manifest + marker, marker))
        self.assertTrue(observed_marker(manifest + "\x1b[?25h" + marker + "\r\n", marker))
        self.assertTrue(observed_marker("check-system-services[160]: " + marker + "\r\n", marker))
        self.assertFalse(observed_marker("check-system-services[160]: " + manifest, marker))

    def test_desktop_waits_for_complete_evidence_and_sent_input(self):
        expected = "CUSTOM_DESKTOP_SESSION_OK"
        markers = success_markers(expected, True)
        # Another unit can emit the root success while journald is still
        # forwarding the app logs. Do not stop QEMU at that early marker.
        early = expected + "\n" + "\n".join(markers[1:-1]) + "\n"
        self.assertFalse(observed_success(early, markers, desktop_input=True, input_sent=True))
        self.assertFalse(observed_success(early + markers[-1], markers, desktop_input=True, input_sent=True))
        complete = early + markers[-1] + "\n"
        self.assertFalse(observed_success(complete, markers, desktop_input=True, input_sent=False))
        self.assertTrue(observed_success(complete, markers, desktop_input=True, input_sent=True))
        self.assertEqual(success_markers(expected, False), [expected])


if __name__ == "__main__":
    unittest.main()
