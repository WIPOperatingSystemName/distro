"""Normal-use VMs retain real private files without running boot qualification."""
from contextlib import ExitStack
import fcntl
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build import cli, vm, vm_session
from distro_build.boot import sha256


class VmSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vm-session-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.image = self.project / "base.img"
        self.image.write_bytes(b"Synthetic lifecycle disk; not a qualified OS image")
        self.code = self.project / "OVMF_CODE.fd"
        self.code.write_bytes(b"read-only firmware code fixture")
        self.variables = self.project / "OVMF_VARS.fd"
        self.variables.write_bytes(b"initial firmware variables fixture")
        self.runtime = {"qemu": "/private-fixture/qemu", "code": str(self.code),
                        "vars": str(self.variables), "data": None,
                        "environment": {"LANG": "C"}, "kind": "test-fixture"}
        # The file-copy/state lifecycle is real. GPT/native/process work is
        # covered separately and must not start QEMU for these unit tests.
        self.validation = self.enterContext(patch.object(vm_session, "validate_disk"))

    def initialize(self, name="custom", image=None, runtime=None):
        image = image or self.image
        runtime = runtime or self.runtime
        directory, paths = vm_session.prepare(self.project, image, name, runtime)
        # Use the same real lock discipline as start().
        with paths["lock"].open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            record = vm_session.initialize(directory, image, runtime)
        return directory, record

    def test_initialization_copies_source_once_and_keeps_files_private(self):
        source_digest, vars_digest = sha256(self.image), sha256(self.variables)
        directory, record = self.initialize()
        self.assertEqual((directory / "disk.img").read_bytes(), self.image.read_bytes())
        self.assertEqual((directory / "OVMF_VARS.fd").read_bytes(), self.variables.read_bytes())
        self.assertEqual(record["base_image_sha256"], source_digest)
        self.assertEqual(record["firmware_code_sha256"], sha256(self.code))
        self.assertNotEqual((directory / "disk.img").stat().st_ino, self.image.stat().st_ino)
        self.assertEqual(sha256(self.image), source_digest)
        self.assertEqual(sha256(self.variables), vars_digest)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for filename in ("disk.img", "OVMF_VARS.fd", "state.json"):
            self.assertEqual(stat.S_IMODE((directory / filename).stat().st_mode), 0o600)
        self.assertEqual(json.loads((directory / "state.json").read_text()), record)

    def test_reuse_keeps_guest_disk_and_vars_even_when_base_and_seed_change(self):
        directory, record = self.initialize()
        original_state = (directory / "state.json").read_bytes()
        (directory / "disk.img").write_bytes(b"user files and installed updates")
        (directory / "OVMF_VARS.fd").write_bytes(b"guest firmware settings")
        replacement = self.project / "new-release.img"
        replacement.write_bytes(b"different release disk")
        self.variables.write_bytes(b"different firmware variable seed")
        with patch.object(vm_session.shutil, "copyfile", side_effect=AssertionError("Existing VM must not reset")):
            reused_directory, reused = self.initialize(image=replacement)
        self.assertEqual(reused_directory, directory)
        self.assertEqual(reused, record)
        self.assertEqual((directory / "disk.img").read_bytes(), b"user files and installed updates")
        self.assertEqual((directory / "OVMF_VARS.fd").read_bytes(), b"guest firmware settings")
        self.assertEqual((directory / "state.json").read_bytes(), original_state)
        self.assertEqual(replacement.read_bytes(), b"different release disk")
        # A retained VM also remains usable after its original image was removed.
        self.image.unlink()
        with patch.object(vm_session.shutil, "copyfile", side_effect=AssertionError("Existing VM must not reset")):
            self.assertEqual(self.initialize(image=self.image)[1], record)

    def test_managed_source_is_bound_to_the_verified_content_object(self):
        objects = self.image.parent / "objects"
        objects.mkdir()
        retained = objects / (sha256(self.image) + ".img")
        retained.write_bytes(self.image.read_bytes())
        directory, record = self.initialize()
        self.assertEqual(record["base_image"], str(retained))
        self.assertEqual(sha256(directory / "disk.img"), sha256(retained))

    def test_corrupt_retained_source_is_refused_without_copying(self):
        objects = self.image.parent / "objects"
        objects.mkdir()
        (objects / (sha256(self.image) + ".img")).write_bytes(b"wrong bytes under a content filename")
        with self.assertRaisesRegex(RuntimeError, "content identity"):
            self.initialize()
        directory = self.project / "out/vms/custom"
        self.assertFalse((directory / "disk.img").exists())
        self.assertFalse((directory / "state.json").exists())

    def test_missing_private_disk_or_vars_is_refused_and_not_recreated(self):
        for filename in ("disk.img", "OVMF_VARS.fd"):
            with self.subTest(filename=filename):
                directory, _ = self.initialize(name=filename.replace(".", "_"))
                (directory / filename).unlink()
                with patch.object(vm_session.shutil, "copyfile", side_effect=AssertionError("Broken VM must not reset")):
                    with self.assertRaisesRegex(RuntimeError, "incomplete"):
                        vm_session.initialize(directory, self.image, self.runtime)
                self.assertFalse((directory / filename).exists())

    def test_broken_state_records_are_refused_without_overwriting_files(self):
        corruptions = {
            "malformed": "{",
            "non_object": [],
            "schema": {"schema": 99},
            "missing_identity": {"schema": 1},
            "wrong_disk": "disk-path",
            "wrong_vars": "vars-path",
        }
        for name, corruption in corruptions.items():
            with self.subTest(name=name):
                directory, record = self.initialize(name=name)
                if corruption == "disk-path":
                    corruption = dict(record, disk=str(self.project / "outside.img"))
                elif corruption == "vars-path":
                    corruption = dict(record, firmware_variables=str(self.project / "outside.fd"))
                state = directory / "state.json"
                state.write_text(corruption if isinstance(corruption, str) else json.dumps(corruption))
                before = {path: path.read_bytes() for path in
                          (state, directory / "disk.img", directory / "OVMF_VARS.fd")}
                with patch.object(vm_session.shutil, "copyfile", side_effect=AssertionError("Broken state must not reset")):
                    with self.assertRaises((RuntimeError, ValueError)):
                        vm_session.initialize(directory, self.image, self.runtime)
                for path, value in before.items():
                    self.assertEqual(path.read_bytes(), value)

    def test_unrecorded_or_partial_preparation_is_preserved_and_refused(self):
        for filename in ("disk.img", "OVMF_VARS.fd", "disk.next"):
            with self.subTest(filename=filename):
                directory, _ = vm_session.prepare(self.project, self.image,
                    "partial_" + filename.replace(".", "_"), self.runtime)
                payload = directory / filename
                payload.write_bytes(b"do not silently replace this private data")
                with self.assertRaises(RuntimeError):
                    vm_session.initialize(directory, self.image, self.runtime)
                self.assertEqual(payload.read_bytes(), b"do not silently replace this private data")
                self.assertFalse((directory / "state.json").exists())

    def test_changed_firmware_code_refuses_existing_vm(self):
        directory, _ = self.initialize()
        before = (directory / "disk.img").read_bytes()
        self.code.write_bytes(b"different incompatible firmware code")
        with self.assertRaisesRegex(RuntimeError, "original matching OVMF"):
            vm_session.initialize(directory, self.image, self.runtime)
        self.assertEqual((directory / "disk.img").read_bytes(), before)

    def test_vm_names_cannot_escape_or_select_another_directory(self):
        for name in ("", ".", "..", "../outside", "folder/name", "/absolute", "name with space", "x" * 49):
            with self.subTest(name=name):
                with self.assertRaisesRegex(RuntimeError, "VM name"):
                    vm_session.prepare(self.project, self.image, name, self.runtime)
        self.assertFalse((self.project / "out/vms").exists())

    def test_private_parent_directory_and_files_cannot_be_symlinks(self):
        outside = self.project / "untouched"
        outside.mkdir()
        (outside / "sentinel").write_bytes(b"untouched")
        parent = self.project / "out/vms"
        parent.parent.mkdir()
        parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            vm_session.prepare(self.project, self.image, "custom", self.runtime)
        parent.unlink()
        parent.mkdir()
        (parent / "custom").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            vm_session.prepare(self.project, self.image, "custom", self.runtime)
        for filename in ("disk.img", "OVMF_VARS.fd", "state.json", "vm.lock", "serial.log",
                         "qemu.log", "command.json", "session.json", "qmp.sock"):
            with self.subTest(filename=filename):
                name = filename.replace(".", "_")
                directory = parent / name
                directory.mkdir()
                (directory / filename).symlink_to(outside / "sentinel")
                with self.assertRaisesRegex(RuntimeError, "symlink"):
                    vm_session.prepare(self.project, self.image, name, self.runtime)
        self.assertEqual(list(outside.iterdir()), [outside / "sentinel"])
        self.assertEqual((outside / "sentinel").read_bytes(), b"untouched")

    def start_fixture(self, *, interrupt=False):
        owner = self
        events = []
        directory = self.project / "out/vms/custom"
        source_digest, vars_digest = sha256(self.image), sha256(self.variables)

        class Process:
            returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                events.append(("wait", timeout))
                if timeout is None:
                    # The manager retains the actual lock for the complete
                    # human session, not just disk preparation.
                    with (directory / "vm.lock").open("a") as contender:
                        with owner.assertRaises(BlockingIOError):
                            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    (directory / "disk.img").write_bytes(b"changes made during normal use")
                    (directory / "OVMF_VARS.fd").write_bytes(b"settings changed during normal use")
                    if interrupt:
                        raise KeyboardInterrupt
                    self.returncode = 0
                elif self.returncode is None:
                    raise AssertionError("Process cleanup must request termination before waiting")
                return self.returncode

            def terminate(self):
                events.append(("terminate",))
                self.returncode = -15

            def kill(self):
                events.append(("kill",))
                self.returncode = -9

        process = Process()

        def launch(command, **kwargs):
            self.command, self.launch_kwargs = command, kwargs
            (directory / "qmp.sock").write_bytes(b"fake socket fixture")
            return process

        with ExitStack() as stack:
            discover = stack.enter_context(patch.object(vm_session, "discover_runtime", return_value=self.runtime))
            stack.enter_context(patch.object(vm_session.os, "access", return_value=False))
            other_run = stack.enter_context(patch.object(vm_session.subprocess, "run", side_effect=AssertionError("No assertion subprocess")))
            popen = stack.enter_context(patch.object(vm_session.subprocess, "Popen", side_effect=launch))
            qualifier = stack.enter_context(patch.object(vm, "run", side_effect=AssertionError("Normal use must not call qualification")))
            stack.enter_context(patch("sys.stderr", new=io.StringIO()))
            result = vm_session.start(self.project, self.image)
        discover.assert_called_once_with(self.project)
        popen.assert_called_once()
        other_run.assert_not_called()
        qualifier.assert_not_called()
        self.assertEqual(sha256(self.image), source_digest)
        self.assertEqual(sha256(self.variables), vars_digest)
        self.assertFalse((directory / "qmp.sock").exists())
        self.assertEqual(json.loads((directory / "session.json").read_text()), result)
        with (directory / "vm.lock").open("a") as after_session:
            fcntl.flock(after_session, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return result, events

    def test_normal_use_has_writable_private_media_and_waits_for_full_session(self):
        result, events = self.start_fixture()
        command = self.command
        joined = " ".join(command)
        self.assertNotIn("snapshot", joined)
        self.assertNotIn("-no-reboot", command)
        self.assertNotIn("-S", command)
        self.assertNotIn("CUSTOM_", joined)
        self.assertIn("gtk,gl=off", command)
        self.assertIn("usb-tablet,bus=xhci.0", command)
        self.assertIn("user,id=network", command)
        self.assertIn("virtio-net-pci,netdev=network", command)
        self.assertIn("if=pflash,format=raw,readonly=on,file=" + str(self.code), command)
        self.assertIn("if=pflash,format=raw,file=" + result["directory"] + "/OVMF_VARS.fd", command)
        self.assertIn("id=bootdisk,if=none,format=raw,file=" + result["disk"], command)
        self.assertEqual(events, [("wait", None)])
        self.assertEqual(result["mode"], "normal-desktop-use")
        self.assertTrue(result["persistent"])
        self.assertFalse(result["boot_assertions"])
        self.assertNotIn("success", result)
        self.assertEqual(self.launch_kwargs["env"]["TMPDIR"], result["directory"])
        self.assertEqual((Path(result["disk"])).read_bytes(), b"changes made during normal use")

    def test_ctrl_c_stops_process_and_keeps_guest_changes(self):
        result, events = self.start_fixture(interrupt=True)
        self.assertEqual(result["exit_code"], -15)
        self.assertEqual(events, [("wait", None), ("terminate",), ("wait", 10)])
        self.assertEqual(Path(result["disk"]).read_bytes(), b"changes made during normal use")
        self.assertEqual((Path(result["directory"]) / "OVMF_VARS.fd").read_bytes(), b"settings changed during normal use")

    def test_real_lock_prevents_second_launch_before_touching_guest_disk(self):
        directory, record = self.initialize()
        disk = directory / "disk.img"
        disk.write_bytes(b"must retain existing guest changes")
        with (directory / "vm.lock").open("a") as active:
            fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(vm_session, "discover_runtime", return_value=self.runtime), \
                 patch.object(vm_session.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    vm_session.start(self.project, self.image)
            launch.assert_not_called()
        self.assertEqual(disk.read_bytes(), b"must retain existing guest changes")
        self.assertEqual(json.loads((directory / "state.json").read_text()), record)

    def test_cli_use_routes_to_normal_image_and_never_calls_boot_check(self):
        for override in (None, self.image):
            with self.subTest(image=override):
                arguments = ["--project", str(self.project), "vm", "--use", "--name", "daily"]
                if override:
                    arguments += ["--image", str(override)]
                with patch.object(vm_session, "start", return_value={"mode": "normal-desktop-use"}) as start, \
                     patch.object(vm, "run", side_effect=AssertionError("No boot check")) as check, \
                     patch.object(cli, "emit"):
                    self.assertEqual(cli.main(arguments), 0)
                start.assert_called_once_with(self.project,
                    override or self.project / "out/images/custom-distro-desktop-use.img", name="daily")
                check.assert_not_called()

    def test_cli_use_rejects_conflicting_qualification_controls(self):
        for extra in (["--interactive"], ["--desktop-input"], ["--output", "test-report"]):
            with self.subTest(extra=extra):
                with patch.object(vm_session, "start") as start, patch.object(vm, "run") as check, \
                     patch("sys.stderr", new=io.StringIO()):
                    self.assertEqual(cli.main(["--project", str(self.project), "vm", "--use", *extra]), 1)
                start.assert_not_called()
                check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
