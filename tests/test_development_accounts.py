"""Development password enrollment must be explicit, scoped and private."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build import development_accounts as accounts


class DevelopmentPasswordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = patch.object(accounts, "vm_directory", return_value=self.root).start()
        self.addCleanup(patch.stopall)
        self.agent = MagicMock()
        patch.object(accounts, "GuestAgent").start().return_value.__enter__.return_value = self.agent
        self.agent.execute.return_value = {"out-data": json.dumps({"profile": "experimental-desktop-dev", "system_shell": True})}

    def test_enrollment_sends_only_stdin_and_returns_no_credential(self):
        result = accounts.enroll_password(self.root, name="dev", password="fixture-password")
        call = self.agent.execute.call_args
        self.assertEqual(call.args, ("/usr/bin/telorgon-test-shell", ["--set-user-password", "custom"]))
        self.assertEqual(call.kwargs, {"input_data": b"fixture-password\n", "capture_output": False})
        self.assertTrue(result["password_enrolled"])
        self.assertNotIn("fixture-password", json.dumps(result))

    def test_non_development_or_automatic_user_guest_is_rejected(self):
        for manifest in [{"profile": "experimental-desktop-use", "system_shell": True},
                         {"profile": "experimental-desktop-dev", "system_shell": False}]:
            self.agent.reset_mock()
            self.agent.execute.return_value = {"out-data": json.dumps(manifest)}
            with self.assertRaisesRegex(RuntimeError, "desktop-dev VM"):
                accounts.enroll_password(self.root, name="dev", password="fixture-password")
            self.assertEqual(self.agent.execute.call_count, 1)

    def test_invalid_password_is_rejected_before_connecting(self):
        for password in ["short", "a" * 513, "pass\nword", "pass\0word", "pass\u0085word"]:
            with self.assertRaises(ValueError):
                accounts.enroll_password(self.root, name="dev", password=password)
        self.directory.assert_not_called()

    def test_prompt_requires_a_terminal_and_matching_confirmation(self):
        with patch.object(accounts.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                accounts.prompt_password()
        with patch.object(accounts.sys.stdin, "isatty", return_value=True), patch.object(accounts.getpass, "getpass", side_effect=["first-password", "second-password"]):
            with self.assertRaisesRegex(ValueError, "do not match"):
                accounts.prompt_password()


if __name__ == "__main__":
    unittest.main()
