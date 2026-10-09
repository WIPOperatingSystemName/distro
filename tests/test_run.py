"""The build-and-run workflow must stop on failure and boot its actual output."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build import apps, cli, compose, runner, vm, vm_session


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
        for profile in ("desktop-use", "systemd", "console"):
            (self.project / "profiles" / f"{profile}.toml").write_text('packages = ["linux", "busybox"]\n')
        sdk = self.project / "out/sdk"
        sdk.mkdir(parents=True)
        self.sysroot = sdk / "fixture"
        (sdk / "current.json").write_text(json.dumps({"sysroot": str(self.sysroot)}))
        self.image = self.project / "out/images/objects/new-image.img"
        self.digest = "a" * 64
        self.entry = cli.main
        self.events = []

    def invoke(self, *arguments, fail_stage=None, sdk_ready=True, boot_ok=True):
        def stage(argv, *, quiet):
            self.assertTrue(quiet)
            self.assertEqual(argv[:2], ["--project", str(self.project)])
            self.events.append(argv[2:])
            return int(argv[2] == fail_stage)

        def image(*args, **kwargs):
            self.events.append(["compose", kwargs["profile_name"]])
            return {"image": {"path": str(self.image), "sha256": self.digest}}

        with ExitStack() as stack:
            stack.enter_context(patch.object(cli, "main", side_effect=stage))
            stack.enter_context(patch.object(cli, "emit"))
            stack.enter_context(patch.object(vm, "discover_runtime"))
            stack.enter_context(patch.object(compose, "runtime_packages", return_value=["linux", "busybox"]))
            stack.enter_context(patch.object(compose, "console_image", side_effect=image))
            stack.enter_context(patch.object(runner, "run"))
            stack.enter_context(patch.object(apps, "preflight", return_value={"ready_to_build": sdk_ready}))
            self.desktop = stack.enter_context(patch.object(vm_session, "start", return_value={"exit_code": 0}))
            self.boot = stack.enter_context(patch.object(vm, "run", return_value={"success": boot_ok}))
            self.stderr = stack.enter_context(patch("sys.stderr", new=io.StringIO()))
            return self.entry(["--project", str(self.project), "run", *arguments])

    def test_default_builds_desktop_then_launches_the_composed_object(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.events[-1], ["compose", "desktop-use"])
        self.assertIn(["bootstrap", "--check"], self.events)
        self.assertIn(["build", "--jobs", "4"], self.events)
        self.assertIn(["apps", "build", "--sysroot", str(self.sysroot), "--jobs", "4", "--online"], self.events)
        self.desktop.assert_called_once_with(self.project, self.image, name="test-" + self.digest[:16])
        self.boot.assert_not_called()

    def test_new_image_digest_selects_a_different_default_saved_disk(self):
        self.invoke()
        first = self.desktop.call_args.kwargs["name"]
        self.digest = "b" * 64
        self.invoke()
        self.assertNotEqual(first, self.desktop.call_args.kwargs["name"])

    def test_explicit_name_preserves_the_chosen_disk(self):
        self.assertEqual(self.invoke("--name", "my-test"), 0)
        self.desktop.assert_called_once_with(self.project, self.image, name="my-test")

    def test_source_override_reaches_loader_and_application_snapshot(self):
        source_root = self.project / "development-sources"
        (self.project / "sources").rename(source_root)
        self.assertEqual(self.invoke("--source-root", str(source_root)), 0)
        self.assertIn(["loader", "--source-root", str(source_root), "--online"], self.events)
        self.assertIn(["apps", "prepare", "--source-root", str(source_root)], self.events)

    def test_offline_is_applied_to_download_toolkit_loader_and_apps(self):
        self.assertEqual(self.invoke("--offline", "--jobs", "2"), 0)
        self.assertIn(["fetch", "--bootstrap", "--offline"], self.events)
        self.assertIn(["native-toolkit", "--jobs", "2"], self.events)
        self.assertIn(["apps", "build", "--sysroot", str(self.sysroot), "--jobs", "2"], self.events)
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
        self.assertNotIn(["apps", "build", "--sysroot", str(self.sysroot), "--jobs", "4", "--online"], self.events)
        self.desktop.assert_not_called()

    def test_missing_sources_fail_before_building(self):
        (self.project / "sources/telorgon/Cargo.toml").unlink()
        self.assertEqual(self.invoke(), 1)
        self.assertEqual(self.events, [])
        self.assertIn("initialize the pinned submodules", self.stderr.getvalue())

    def test_systemd_headless_uses_runtime_closure_and_propagates_boot_failure(self):
        self.assertEqual(self.invoke("--profile", "systemd", "--headless", "--output", "out/check", boot_ok=False), 1)
        self.assertIn(["build", "linux", "busybox", "--jobs", "4"], self.events)
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
        with patch.object(runner, "seed_report", return_value={"large": "receipt"}), patch.object(cli, "emit") as emit:
            self.assertEqual(self.entry(["doctor"], quiet=True), 0)
            emit.assert_not_called()
            self.assertEqual(self.entry(["doctor"]), 0)
            emit.assert_called_once_with({"large": "receipt"})
        with patch.object(runner, "seed_report", side_effect=RuntimeError("missing seed")), patch("sys.stderr", new=io.StringIO()) as stderr:
            self.assertEqual(self.entry(["doctor"], quiet=True), 1)
            self.assertIn("missing seed", stderr.getvalue())

    def test_nonpositive_jobs_and_deadlines_are_rejected(self):
        for flag in ("--jobs", "--timeout"):
            with self.subTest(flag=flag), patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.parser().parse_args(["run", flag, "0"])
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
