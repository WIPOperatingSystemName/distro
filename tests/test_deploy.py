"""Development deployment must verify bytes and use one real package transaction."""
import base64
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.deploy import install, vm_directory
from distro_build.guest_agent import GuestAgent
from distro_build.packaging import export_package
import distro_build.deploy as deployment


class AgentProtocolTests(unittest.TestCase):
    def serve(self, handler):
        temporary = tempfile.TemporaryDirectory(prefix="distro-ga-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "agent.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)
        errors = []

        def run():
            try:
                with server.accept()[0] as connection:
                    stream = connection.makefile("rb")
                    while line := stream.readline():
                        request = json.loads(line.lstrip(b"\xff"))
                        response = handler(request)
                        if response is None:
                            response = {"return": request["arguments"]["id"]}
                        response["id"] = request["id"]
                        encoded = json.dumps(response).encode() + b"\n"
                        # Delimiter plus fragmented replies exercise real framing.
                        if request["execute"] == "guest-sync-delimited":
                            encoded = b"old partial response\xff" + encoded
                        connection.sendall(encoded[:7])
                        connection.sendall(encoded[7:])
            except Exception as error:
                errors.append(error)
            finally:
                server.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        def cleanup():
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

        self.addCleanup(cleanup)
        return path

    def test_file_transfer_handles_partial_writes_without_losing_bytes(self):
        payload = bytearray()
        commands = []

        def handler(request):
            command = request["execute"]
            commands.append(command)
            if command == "guest-sync-delimited":
                return None
            if command == "guest-file-open":
                return {"return": 42}
            if command == "guest-file-write":
                chunk = base64.b64decode(request["arguments"]["buf-b64"])
                count = min(23451, len(chunk))
                payload.extend(chunk[:count])
                return {"return": {"count": count}}
            return {"return": {}}

        path = self.serve(handler)
        source = path.parent / "package"
        source.write_bytes(bytes(range(256)) * 1400)
        with GuestAgent(path) as agent:
            agent.transfer(source, "/var/lib/custom-distro/deploy/package")
        self.assertEqual(bytes(payload), source.read_bytes())
        self.assertEqual(commands[-2:], ["guest-file-flush", "guest-file-close"])

    def test_agent_command_failure_is_reported_with_captured_stderr(self):
        def handler(request):
            if request["execute"] == "guest-sync-delimited":
                return None
            if request["execute"] == "guest-exec":
                return {"return": {"pid": 123}}
            return {"return": {"exited": True, "exitcode": 1,
                "err-data": base64.b64encode(b"dependency rejected").decode()}}

        with GuestAgent(self.serve(handler)) as agent:
            with self.assertRaisesRegex(RuntimeError, "dependency rejected"):
                agent.execute("/usr/bin/pacman", ["-U", "package"])

    def test_zero_progress_closes_guest_file_and_fails(self):
        commands = []

        def handler(request):
            command = request["execute"]
            commands.append(command)
            if command == "guest-sync-delimited":
                return None
            return {"return": 42 if command == "guest-file-open" else {"count": 0}}

        path = self.serve(handler)
        source = path.parent / "package"
        source.write_bytes(b"package")
        with GuestAgent(path) as agent:
            with self.assertRaisesRegex(RuntimeError, "progress"):
                agent.transfer(source, "/package")
        self.assertEqual(commands[-1], "guest-file-close")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="distro-deploy-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifacts = []
        for name in ("telorgon-shell", "telorgon-settings-app"):
            stage = self.root / name
            (stage / "usr/bin").mkdir(parents=True)
            (stage / "usr/bin" / name).write_text(name)
            artifact = export_package(stage, self.root / "packages", {
                "name": name, "version": "0.1.dev123", "revision": 1,
                "arch": "x86_64", "description": "deployment fixture", "license": "MIT"}, source_date_epoch=1756684800)
            self.artifacts.append({"package": name, "path": str(artifact.path), "sha256": artifact.sha256})

    def agent(self, *, corrupt=False, fail=False):
        class Fixture:
            def __init__(self):
                self.calls = []
                self.files = {}

            def transfer(self, source, destination):
                self.files[destination] = source.read_bytes() + (b"corrupt" if corrupt else b"")

            def execute(self, executable, arguments):
                self.calls.append((executable, arguments))
                output = ""
                if arguments[0] == "sha256sum":
                    output = hashlib.sha256(self.files[arguments[1]]).hexdigest() + "  file"
                if arguments[0] == "-U" and fail:
                    raise RuntimeError("ALPM dependency rejection")
                if arguments[0] == "-Q":
                    output = arguments[1] + " 0.1.dev123-1\n"
                return {"out-data": output, "err-data": "", "exitcode": 0}

        return Fixture()

    def test_packages_installed_together_and_versions_checked_before_restart(self):
        agent = self.agent()
        result = install(agent, self.artifacts)
        transactions = [args for exe, args in agent.calls if exe == "/usr/bin/pacman" and args[0] == "-U"]
        self.assertEqual(len(transactions), 1)
        self.assertEqual(len(transactions[0]), 4)
        self.assertTrue(result["desktop_restarted"])
        query = next(i for i, (_, args) in enumerate(agent.calls) if args[0] == "-Q")
        restart = next(i for i, (_, args) in enumerate(agent.calls) if args[0] == "restart")
        self.assertLess(query, restart)

    def test_no_restart_keeps_current_session_running(self):
        agent = self.agent()
        result = install(agent, self.artifacts, restart=False)
        self.assertFalse(result["desktop_restarted"])
        self.assertFalse(any(exe == "/usr/bin/systemctl" for exe, _ in agent.calls))

    def test_bad_transfer_never_invokes_package_manager(self):
        agent = self.agent(corrupt=True)
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            install(agent, self.artifacts)
        self.assertFalse(any(exe == "/usr/bin/pacman" for exe, _ in agent.calls))

    def test_failed_transaction_never_restarts_desktop(self):
        agent = self.agent(fail=True)
        with self.assertRaisesRegex(RuntimeError, "dependency rejection"):
            install(agent, self.artifacts)
        self.assertFalse(any(exe == "/usr/bin/systemctl" for exe, _ in agent.calls))

    def test_restart_waits_when_a_readiness_file_precedes_its_live_socket(self):
        agent = self.agent()
        install(agent, self.artifacts)
        script = next(args[1] for executable, args in agent.calls if executable == "/bin/sh")
        script = script.replace("/run/user/1000", str(self.root)).replace("sleep 1", "sleep 0.01")
        pointer = self.root / "telorgon-wayland-socket"
        pointer.write_text(str(self.root / "wayland-old") + "\n")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)

        def ready():
            time.sleep(0.1)
            server.bind(str(self.root / "wayland-new"))
            pointer.write_text(str(self.root / "wayland-new") + "\n")

        thread = threading.Thread(target=ready)
        thread.start()
        try:
            result = subprocess.run(["/bin/sh", "-ec", script], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            thread.join(2)

    def test_tampered_build_output_is_rejected_before_transfer(self):
        Path(self.artifacts[0]["path"]).write_bytes(b"tampered")
        agent = self.agent()
        with self.assertRaisesRegex(RuntimeError, "build receipt"):
            install(agent, self.artifacts)
        self.assertEqual(agent.calls, [])

    def test_vm_names_and_symlinks_cannot_redirect_deployment(self):
        for name in ("../other", "/tmp", "", "a" * 49):
            with self.assertRaisesRegex(RuntimeError, "name"):
                vm_directory(self.root, name)
        parent = self.root / "out/vms"
        parent.mkdir(parents=True)
        (parent / "dev").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            vm_directory(self.root, "dev")

    def test_agent_failure_does_not_compile_or_leave_a_stale_success_receipt(self):
        directory = self.root / "out/vms/dev"
        directory.mkdir(parents=True)
        (directory / "state.json").write_text(json.dumps({"base_image_sha256": "a" * 64}))
        (directory / "deployment.json").write_text('{"success":true}')
        with patch.object(deployment, "vm_directory", return_value=directory), \
             patch.object(deployment.apps, "catalog", return_value={"telorgon-shell": {}}), \
             patch.object(deployment, "GuestAgent", side_effect=RuntimeError("agent unavailable")), \
             patch.object(deployment.apps, "build_app") as build:
            with self.assertRaisesRegex(RuntimeError, "agent unavailable"):
                deployment.deploy(self.root, ["telorgon-shell"], name="dev")
        build.assert_not_called()
        record = json.loads((directory / "deployment.json").read_text())
        self.assertFalse(record["success"])
        self.assertEqual(record["stage"], "preflight")
        self.assertIn("agent unavailable", record["error"])


if __name__ == "__main__":
    unittest.main()
