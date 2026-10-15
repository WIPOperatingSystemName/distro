"""The build-and-run workflow must stop on failure and boot its actual output."""
from contextlib import ExitStack
import fcntl
import io
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build import apps, cli, compose, runner, vm, vm_session
from distro_build.boot import sha256


class RunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="distro-run-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        for name in ("telorgon", "telorgon-bootloader"):
            directory = self.project / "sources" / name
            directory.mkdir(parents=True)
            (directory / "Cargo.toml").write_text("[workspace]\n")
        (self.project / "profiles").mkdir()
        for profile in ("desktop-use", "desktop-dev", "systemd", "console"):
            (self.project / "profiles" / f"{profile}.toml").write_text('packages = ["linux", "busybox"]\n[system_shell]\npackages = ["linux", "busybox", "fixture-app"]\n')
        sdk = self.project / "out/sdk"
        sdk.mkdir(parents=True)
        self.sysroot = sdk / "fixture"
        (sdk / "current.json").write_text(json.dumps({"sysroot": str(self.sysroot)}))
        self.image = self.project / "out/images/objects/new-image.img"
        self.digest = "a" * 64
        self.identity = "c" * 64
        self.entry = cli.main
        self.events = []

    def invoke(self, *arguments, fail_stage=None, sync_error=None, sdk_ready=True, boot_ok=True, desktop_start=None, progress_events=False, closure=None):
        def stage(argv, *, quiet):
            self.assertTrue(quiet)
            self.assertEqual(argv[:2], ["--project", str(self.project)])
            self.events.append(argv[2:])
            return int(argv[2] == fail_stage)

        def image(*args, **kwargs):
            self.composed = kwargs
            self.events.append(["compose", kwargs["profile_name"]])
            return {"identity": self.identity, "image": {"path": str(self.image), "sha256": self.digest}}

        with ExitStack() as stack:
            stack.enter_context(patch.object(cli, "default_jobs", return_value=8))
            self.sync = stack.enter_context(patch.object(cli.upstream, "synchronize", side_effect=sync_error))
            stack.enter_context(patch.object(cli, "main", side_effect=stage))
            stack.enter_context(patch.object(cli, "emit", wraps=cli.emit if progress_events else None))
            # These tests exercise orchestration; real inventories are covered in test_run_cache.
            stack.enter_context(patch.object(cli.DevelopmentCache, "inventory", return_value="fixture-inputs"))
            stack.enter_context(patch.object(vm, "discover_runtime"))
            stack.enter_context(patch.object(compose, "runtime_packages", return_value=closure or ["linux", "busybox"]))
            stack.enter_context(patch.object(compose, "console_image", side_effect=image))
            stack.enter_context(patch.object(runner, "run"))
            stack.enter_context(patch.object(apps, "preflight", return_value={"ready_to_build": sdk_ready}))
            stack.enter_context(patch.object(apps, "catalog", return_value={"fixture-app": {}}))
            self.desktop = stack.enter_context(patch.object(vm_session, "start", side_effect=desktop_start,
                                                          return_value={"exit_code": 0}))
            self.boot = stack.enter_context(patch.object(vm, "run", return_value={"success": boot_ok}))
            self.stderr = stack.enter_context(patch("sys.stderr", new=io.StringIO()))
            return self.entry(["--project", str(self.project), "run", *arguments])

    def test_default_builds_desktop_then_launches_the_composed_object(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.events[-1], ["compose", "desktop-use"])
        self.assertIn(["bootstrap", "--check"], self.events)
        self.assertIn(["bootstrap", "--jobs", "8"], self.events)
        self.assertIn(["native-toolkit", "--jobs", "8", "--fetch"], self.events)
        self.assertIn(["build", "--jobs", "8", "--package-workers", "2"], self.events)
        self.assertIn(["apps", "build", "--sysroot", str(self.sysroot), "--jobs", "8", "--source-root", str(self.project / "sources"), "--online"], self.events)
        self.desktop.assert_called_once_with(self.project, self.image, name="test-" + self.identity[:16])
        self.boot.assert_not_called()

    def test_changed_build_identity_selects_a_different_default_saved_disk(self):
        self.invoke()
        first = self.desktop.call_args.kwargs["name"]
        self.identity = "d" * 64
        self.invoke()
        self.assertNotEqual(first, self.desktop.call_args.kwargs["name"])

    def test_system_shell_selects_its_packages_and_composes_its_policy(self):
        self.assertEqual(self.invoke("--profile", "desktop-dev", "--system-shell", "--name", "system-shell",
                                     closure=["linux", "busybox", "fixture-app"]), 0)
        self.assertTrue(self.composed["system_shell"])
        self.assertIn(["build", "linux", "busybox", "--jobs", "8", "--package-workers", "2"], self.events)
        self.assertIn(["apps", "build", "fixture-app", "--sysroot", str(self.sysroot), "--jobs", "8",
                       "--source-root", str(self.project / "sources"), "--online"], self.events)
        self.desktop.assert_called_once_with(self.project, self.image, name="system-shell", development=True)

    def test_system_shell_rejects_console_before_build_or_boot(self):
        self.assertEqual(self.invoke("--profile", "console", "--system-shell"), 1)
        self.assertEqual(self.events, [])
        self.boot.assert_not_called()

    def test_development_profile_default_selects_login_shell_and_its_package_closure(self):
        profile = self.project / "profiles/desktop-dev.toml"
        profile.write_text(profile.read_text().replace("[system_shell]", "[system_shell]\ndefault=true"))
        self.assertEqual(self.invoke("--profile", "desktop-dev", closure=["linux", "busybox", "fixture-app"]), 0)
        self.assertTrue(self.composed["system_shell"])
        self.assertIn(["build", "linux", "busybox", "--jobs", "8", "--package-workers", "2"], self.events)
        self.assertIn("fixture-app", next(event for event in self.events if event[:2] == ["apps", "build"]))

    def test_user_shell_override_preserves_automatic_desktop_choice(self):
        profile = self.project / "profiles/desktop-dev.toml"
        profile.write_text(profile.read_text().replace("[system_shell]", "[system_shell]\ndefault=true"))
        self.assertEqual(self.invoke("--profile", "desktop-dev", "--user-shell"), 0)
        self.assertFalse(compose.system_shell_selection(
            tomllib.loads(profile.read_text()), self.composed.get("system_shell")))

    def test_default_image_alias_tracks_profile_mode_and_keeps_overrides_separate(self):
        dev = {"system_shell": {"default": True}}
        self.assertEqual(compose.desktop_image_name("desktop-dev", dev, True), "custom-distro-desktop-dev.img")
        self.assertEqual(compose.desktop_image_name("desktop-dev", dev, False), "custom-distro-desktop-dev-user-shell.img")
        self.assertEqual(compose.desktop_image_name("desktop-use", {}, False), "custom-distro-desktop-use.img")
        self.assertEqual(compose.desktop_image_name("desktop-use", {}, True), "custom-distro-desktop-use-system-shell.img")

    def test_cold_run_progress_covers_the_os_image_and_vm(self):
        with patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(self.invoke(progress_events=True), 0)
        output = self.stderr.getvalue()
        for name in ("Component updates", "Host checks", "Source downloads", "Toolchain", "Package tools", "OS packages",
                     "Bootloader", "Desktop SDK", "Desktop applications", "OS image", "VM preparation"):
            self.assertIn(name, output)
        self.assertIn("100% 13/13 steps", output)
        self.assertIn("VM started", output)

    def test_progress_failure_does_not_claim_completion_or_launch_a_vm(self):
        self.assertEqual(self.invoke(fail_stage="build", progress_events=True), 1)
        self.assertIn("OS packages: Failed", self.stderr.getvalue())
        self.assertNotIn("100%", self.stderr.getvalue())
        self.desktop.assert_not_called()

    def test_no_progress_keeps_json_events_without_creating_a_progress_log(self):
        with patch("sys.stdout", new=io.StringIO()) as output:
            self.assertEqual(self.invoke("--no-progress", progress_events=True), 0)
        self.assertIn('"event": "stage"', output.getvalue())
        self.assertNotIn("Build progress", self.stderr.getvalue())
        self.assertFalse(list((self.project / "out/logs").glob("run-*.jsonl")))

    def test_development_profile_explicitly_enables_named_guest_channel(self):
        self.assertEqual(self.invoke("--profile", "desktop-dev", "--name", "dev"), 0)
        self.desktop.assert_called_once_with(self.project, self.image, name="dev", development=True)

    def test_recomposed_image_preserves_saved_guest_data_for_the_same_build(self):
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(b"Synthetic disk with first filesystem timestamps")
        code = self.project / "out/OVMF_CODE.fd"
        code.write_bytes(b"firmware code fixture")
        variables = self.project / "out/OVMF_VARS.fd"
        variables.write_bytes(b"firmware variables fixture")
        runtime = {"code": str(code), "vars": str(variables)}

        def initialize(project, image, *, name):
            directory, paths = vm_session.prepare(project, image, name, runtime)
            with paths["lock"].open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return vm_session.initialize(directory, image, runtime)

        # Real private-file/state lifecycle; synthetic disks are not bootable.
        # Disk-format checks and QEMU launch are covered by separate tests.
        with patch.object(vm_session, "validate_disk"):
            self.digest = sha256(self.image)
            self.assertEqual(self.invoke(desktop_start=initialize), 0)
            first_name = self.desktop.call_args.kwargs["name"]
            directory = self.project / "out/vms" / first_name
            original_state = (directory / "state.json").read_bytes()
            (directory / "disk.img").write_bytes(b"saved guest files and settings")
            (directory / "OVMF_VARS.fd").write_bytes(b"saved guest firmware settings")
            self.image.write_bytes(b"Synthetic disk with different filesystem timestamps")
            self.digest = sha256(self.image)
            self.assertNotEqual(json.loads(original_state)["base_image_sha256"], self.digest)
            self.assertEqual(self.invoke(desktop_start=initialize), 0)
        self.assertEqual(self.desktop.call_args.kwargs["name"], first_name)
        self.assertEqual((directory / "disk.img").read_bytes(), b"saved guest files and settings")
        self.assertEqual((directory / "OVMF_VARS.fd").read_bytes(), b"saved guest firmware settings")
        self.assertEqual((directory / "state.json").read_bytes(), original_state)

    def test_explicit_name_preserves_the_chosen_disk(self):
        self.assertEqual(self.invoke("--name", "my-test"), 0)
        self.desktop.assert_called_once_with(self.project, self.image, name="my-test")

    def test_source_override_reaches_loader_and_application_snapshot(self):
        source_root = self.project / "development-sources"
        (self.project / "sources").rename(source_root)
        self.assertEqual(self.invoke("--source-root", str(source_root)), 0)
        self.assertIn(["loader", "--source-root", str(source_root), "--online"], self.events)
        self.assertIn(["apps", "build", "--sysroot", str(self.sysroot), "--jobs", "8", "--source-root", str(source_root), "--online"], self.events)
        self.sync.assert_called_once()
        self.assertEqual(self.sync.call_args.args[:2], (self.project, source_root))

    def test_offline_is_applied_to_download_toolkit_loader_and_apps(self):
        self.assertEqual(self.invoke("--offline", "--jobs", "2"), 0)
        self.assertIn(["fetch", "--bootstrap", "--offline"], self.events)
        self.assertIn(["bootstrap", "--jobs", "2"], self.events)
        self.assertIn(["native-toolkit", "--jobs", "2"], self.events)
        self.assertIn(["build", "--jobs", "2", "--package-workers", "2"], self.events)
        self.assertIn(["apps", "build", "--sysroot", str(self.sysroot), "--jobs", "2", "--source-root", str(self.project / "sources"), "--offline"], self.events)
        self.assertFalse(any("--online" in event or "--fetch" in event for event in self.events))

    def test_failed_stage_stops_before_composition_or_boot(self):
        self.assertEqual(self.invoke(fail_stage="build"), 1)
        self.assertEqual(self.events[-1][0], "build")
        self.assertIn("build-and-run stopped", self.stderr.getvalue())
        self.desktop.assert_not_called()
        self.boot.assert_not_called()

    def test_failed_sdk_preflight_stops_before_apps_or_image(self):
        self.assertEqual(self.invoke(sdk_ready=False), 1)
        self.assertNotIn("compose", [event[0] for event in self.events])
        self.assertNotIn(["apps", "build", "--sysroot", str(self.sysroot), "--jobs", "8", "--source-root", str(self.project / "sources"), "--online"], self.events)
        self.desktop.assert_not_called()

    def test_failed_source_update_stops_before_building(self):
        self.assertEqual(self.invoke(sync_error=RuntimeError("local history diverges")), 1)
        self.assertEqual(self.events, [])
        self.assertIn("local history diverges", self.stderr.getvalue())
        self.desktop.assert_not_called()

    def test_systemd_headless_uses_runtime_closure_and_propagates_boot_failure(self):
        self.assertEqual(self.invoke("--profile", "systemd", "--headless", "--output", "out/check", boot_ok=False), 1)
        self.assertIn(["build", "linux", "busybox", "--jobs", "8", "--package-workers", "2"], self.events)
        self.assertFalse(any(event[0] == "apps" for event in self.events))
        self.boot.assert_called_once_with(self.image, self.project / "out/check", timeout=600,
            headless=True, interactive=False, project=self.project, expect="CUSTOM_SYSTEMD_RUNTIME_OK")
        self.desktop.assert_not_called()

    def test_console_window_stays_open_after_startup(self):
        self.assertEqual(self.invoke("--profile", "console", "--timeout", "300"), 0)
        self.assertTrue(self.boot.call_args.kwargs["interactive"])
        self.assertFalse(self.boot.call_args.kwargs["headless"])
        self.assertEqual(self.boot.call_args.kwargs["expect"], "CUSTOM_DISTRO_PERSISTENT_ROOT_OK")

    def test_conflicting_vm_options_fail_before_building(self):
        for arguments in (("--headless",), ("--output", "out/check"), ("--profile", "console", "--name", "custom")):
            with self.subTest(arguments=arguments):
                self.events = []
                self.assertEqual(self.invoke(*arguments), 1)
                self.assertEqual(self.events, [])

    def test_quiet_stages_hide_receipts_and_keep_failure_diagnostics(self):
        with patch.object(runner, "seed_report", return_value={"large": "receipt"}), \
             patch("distro_build.host.python_venv_report", return_value={"ready": True}), \
             patch.object(cli, "emit") as emit:
            self.assertEqual(self.entry(["doctor"], quiet=True), 0)
            emit.assert_not_called()
            self.assertEqual(self.entry(["doctor"]), 0)
            emit.assert_called_once_with({"large": "receipt", "python_venv": {"ready": True}})
        with patch.object(runner, "seed_report", side_effect=RuntimeError("missing seed")), patch("sys.stderr", new=io.StringIO()) as stderr:
            self.assertEqual(self.entry(["doctor"], quiet=True), 1)
            self.assertIn("missing seed", stderr.getvalue())

    def test_timeout_limits_are_checked_before_building(self):
        for profile in ("console", "systemd"):
            with self.subTest(profile=profile):
                with self.assertRaises(SystemExit) as error:
                    self.invoke("--profile", profile, "--timeout", "7200")
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(self.events, [])
                self.boot.assert_not_called()
        for timeout in (1, 3600):
            with self.subTest(timeout=timeout):
                self.assertEqual(self.invoke("--profile", "console", "--timeout", str(timeout)), 0)
                self.assertEqual(self.boot.call_args.kwargs["timeout"], timeout)

    def test_nonpositive_jobs_and_deadlines_are_rejected(self):
        for flag in ("--jobs", "--timeout"):
            with self.subTest(flag=flag), patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.parser().parse_args(["run", flag, "0"])
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
