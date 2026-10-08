"""Interactive startup must retain QEMU; automated runs must remain bounded."""
from contextlib import ExitStack
from pathlib import Path
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build import cli, vm


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, interval):
        self.now += interval


class VmInteractiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vm-interactive-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "fixture.img"
        self.image.write_bytes(b"VM lifecycle fixture, not a qualified OS image")
        self.variables = self.root / "firmware-vars.fd"
        self.variables.write_bytes(b"private firmware seed")
        self.code = self.root / "firmware-code.fd"
        self.code.write_bytes(b"read-only firmware seed")
        self.output = self.root / "run"
        self.clock = Clock()
        self.events = []
        self.command = None

    def run_fixture(self, *, interactive=False, ready=True, wait_action="close", startup_interrupt=False):
        owner = self
        class Process:
            returncode = None
            quitting = False

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                owner.events.append(("wait", timeout))
                if timeout is None:
                    # Model a human keeping the window open well beyond the
                    # startup deadline, then either closing it or pressing ^C.
                    owner.clock.now += 100
                    if wait_action == "interrupt":
                        raise KeyboardInterrupt
                    self.returncode = 0
                elif self.quitting:
                    self.returncode = 0
                else:
                    raise AssertionError("Cleanup waited before requesting QEMU termination")
                return self.returncode

            def terminate(self):
                owner.events.append(("terminate",))
                self.returncode = -15

            def kill(self):
                owner.events.append(("kill",))
                self.returncode = -9

        process = Process()
        class Connection:
            def __init__(self, path):
                owner.assertEqual(path, owner.output / "qmp.sock")
                self.events = []
                owner.events.append(("connected",))

            def execute(self, command, arguments=None):
                owner.events.append(("qmp", command))
                if command == "quit":
                    process.quitting = True

            def close(self):
                owner.events.append(("closed",))

        def launch(command, **kwargs):
            self.command = command
            self.assertIn("snapshot=on", " ".join(command))
            (self.output / "qmp.sock").write_bytes(b"fake private QMP listener")
            if ready:
                (self.output / "serial.log").write_text("CUSTOM_TEST_STARTUP_OK\n")
            self.events.append(("launched",))
            return process

        runtime = {"qemu": "/private-test/qemu", "vars": str(self.variables),
                   "code": str(self.code), "data": None, "environment": {}, "kind": "test-fixture"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(vm, "validate_disk"))
            stack.enter_context(patch.object(vm, "discover_runtime", return_value=runtime))
            stack.enter_context(patch.object(vm.subprocess, "Popen", side_effect=launch))
            stack.enter_context(patch.object(vm, "Qmp", Connection))
            stack.enter_context(patch.object(vm.time, "monotonic", side_effect=self.clock.monotonic))
            stack.enter_context(patch.object(vm.time, "sleep", side_effect=KeyboardInterrupt if startup_interrupt else self.clock.sleep))
            stack.enter_context(patch("sys.stderr", new=io.StringIO()))
            result = vm.run(self.image, self.output, timeout=1, expect="CUSTOM_TEST_STARTUP_OK",
                            acceleration="tcg", interactive=interactive, headless=not interactive)
        self.assertFalse((self.output / "qmp.sock").exists())
        self.assertEqual(json.loads((self.output / "result.json").read_text()), result)
        self.assertEqual((self.output / "OVMF_VARS.fd").read_bytes(), self.variables.read_bytes())
        self.assertEqual(self.variables.read_bytes(), b"private firmware seed")
        return result

    def test_ready_interactive_vm_stays_open_past_startup_timeout(self):
        result = self.run_fixture(interactive=True)
        self.assertTrue(result["success"])
        self.assertTrue(result["interactive"])
        self.assertGreater(result["elapsed_seconds"], 1)
        self.assertIn(("wait", None), self.events)
        self.assertLess(self.events.index(("qmp", "screendump")), self.events.index(("wait", None)))
        self.assertNotIn(("qmp", "quit"), self.events)
        self.assertEqual(self.command[self.command.index("-display") + 1], "gtk,gl=off")
        self.assertIn("usb-tablet,bus=xhci.0", self.command)

    def test_default_success_stops_without_unbounded_wait(self):
        result = self.run_fixture()
        self.assertTrue(result["success"])
        self.assertFalse(result["interactive"])
        self.assertNotIn(("wait", None), self.events)
        self.assertIn(("qmp", "quit"), self.events)
        self.assertIn(("wait", 5), self.events)
        self.assertLess(self.events.index(("qmp", "screendump")), self.events.index(("qmp", "quit")))
        self.assertNotIn("usb-tablet,bus=xhci.0", self.command)

    def test_default_missing_startup_marker_remains_bounded(self):
        result = self.run_fixture(ready=False)
        self.assertFalse(result["success"])
        self.assertIn("within 1 seconds", result["error"])
        self.assertGreaterEqual(result["elapsed_seconds"], 1)
        self.assertLess(result["elapsed_seconds"], 2)
        self.assertNotIn(("wait", None), self.events)
        self.assertIn(("qmp", "quit"), self.events)

    def test_interactive_failure_does_not_enter_the_human_wait(self):
        result = self.run_fixture(interactive=True, ready=False)
        self.assertFalse(result["success"])
        self.assertNotIn(("wait", None), self.events)
        self.assertIn(("qmp", "quit"), self.events)

    def test_ctrl_c_after_startup_quits_and_preserves_startup_receipt(self):
        result = self.run_fixture(interactive=True, wait_action="interrupt")
        self.assertTrue(result["success"])
        self.assertIsNone(result["error"])
        self.assertLess(self.events.index(("wait", None)), self.events.index(("qmp", "quit")))
        self.assertLess(self.events.index(("qmp", "quit")), self.events.index(("closed",)))
        self.assertIn(("wait", 5), self.events)

    def test_ctrl_c_during_startup_quits_and_records_incomplete_verification(self):
        result = self.run_fixture(interactive=True, ready=False, startup_interrupt=True)
        self.assertFalse(result["success"])
        self.assertIn("before startup verification completed", result["error"])
        self.assertNotIn(("wait", None), self.events)
        self.assertIn(("qmp", "quit"), self.events)
        self.assertIn(("closed",), self.events)

    def test_cli_interactive_implies_a_visible_window(self):
        with patch.object(vm, "run", return_value={"success": True}) as run, patch.object(cli, "emit"):
            code = cli.main(["--project", str(self.root), "vm", "--interactive", "--output", "try-vm"])
        self.assertEqual(code, 0)
        self.assertTrue(run.call_args.kwargs["interactive"])
        self.assertFalse(run.call_args.kwargs["headless"])
        self.assertEqual(run.call_args.args[1], self.root / "try-vm")


if __name__ == "__main__":
    unittest.main()
