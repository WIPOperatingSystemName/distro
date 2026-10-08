import subprocess
import unittest

from distro_build.packaging.pam_test import CHECK_SCRIPT, _locked_custom_entry
from distro_build.packaging.toolkit import ToolkitError


class PamFixtureTests(unittest.TestCase):
    def test_initial_scope_requires_one_locked_normal_account(self):
        for lock in ("!*", "!", "*", "!previous-hash"):
            line = f"custom:{lock}:20332:0:99999:7:::"
            self.assertEqual(_locked_custom_entry("root:!*:20332:0:99999:7:::\n" + line + "\n"), line + "\n")
        for invalid in ("root:!*:20332:0:99999:7:::\n", "custom::20332:0:99999:7:::\n",
                        "custom:$6$fixture:20332:0:99999:7:::\n", "custom:!*:20332\n",
                        "custom:!*:20332:0:99999:7:::\ncustom:!*:20332:0:99999:7:::\n"):
            with self.assertRaises(ToolkitError):
                _locked_custom_entry(invalid)

    def test_enrollment_preserves_other_accounts_and_aging_fields(self):
        # Exercise the same shell field operation on synthetic input only. This
        # does not touch host shadow/PAM/services or run a mock authentication.
        script = '''hash='$6$fixture-hash'
while IFS= read -r entry || [ -n "$entry" ]; do
    case "$entry" in
        custom:*) printf 'custom:%s:%s\\n' "$hash" "${entry#custom:*:}" ;;
        *) printf '%s\\n' "$entry" ;;
    esac
done
'''
        original = "root:!*:20332:0:99999:7:::\ncustom:!*:20332:2:555:3:8:22222:\nmessagebus:!*:20332:0:99999:7:::\n"
        result = subprocess.run(["/bin/sh", "-c", script], input=original, text=True,
                                capture_output=True, check=True).stdout.splitlines()
        before = original.splitlines()
        self.assertEqual(result[0], before[0])
        self.assertEqual(result[2], before[2])
        self.assertEqual(result[1].split(":")[2:], before[1].split(":")[2:])
        self.assertEqual(result[1].split(":")[:2], ["custom", "$6$fixture-hash"])

    def test_guest_script_is_valid_shell(self):
        subprocess.run(["/bin/sh", "-n"], input=CHECK_SCRIPT, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
