"""Content-driven updates, compatibility guards and package-only transactions."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build import apps, development, upstream
from distro_build.cli import parser
from distro_build.source_projects import catalog as source_catalog

PROJECT = Path(__file__).resolve().parents[1]


class UpstreamTests(unittest.TestCase):
    def test_fetch_detects_missing_commits_without_changing_dirty_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin, local = root / "origin", root / "local"
            def git(path, *args):
                return subprocess.run(["git", "-C", str(path), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "init", "-b", "main", str(origin)], check=True, capture_output=True)
            git(origin, "config", "user.name", "Fixture")
            git(origin, "config", "user.email", "fixture@example.invalid")
            (origin / "code").write_text("initial")
            git(origin, "add", "."); git(origin, "commit", "-m", "Initial")
            subprocess.run(["git", "clone", str(origin), str(local)], check=True, capture_output=True)
            initial = git(local, "rev-parse", "HEAD")
            (local / "code").write_text("local draft")
            (origin / "code").write_text("new upstream")
            git(origin, "commit", "-am", "Update")
            record = upstream.check_repository(local, str(origin))
            self.assertEqual(record["behind"], 1)
            self.assertEqual(git(local, "rev-parse", "HEAD"), initial)
            self.assertEqual((local / "code").read_text(), "local draft")
            self.assertEqual(upstream.check_repository(local, str(origin), offline=True)["behind"], 1)
            with self.assertRaisesRegex(RuntimeError, "canonical"):
                upstream.check_repository(local, "https://example.invalid/other")


class CatalogTests(unittest.TestCase):
    def test_new_application_is_discovered_and_its_inputs_are_snapshotted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source-projects.toml").write_text('schema=1\n[projects.terminal]\ninputs=["Cargo.toml","Cargo.lock","src"]\n')
            recipe = root / "packages/terminal/application.toml"
            recipe.parent.mkdir(parents=True)
            recipe.write_text('''schema=1
[package]
name="new-terminal"
version="1.0"
revision=1
[source]
kind="local-content-snapshot"
primary="terminal"
projects=["terminal"]
[build]
adapter="target-cargo"
binary="terminal"
[runtime]
packages=[]
[requirements]
link_libraries=[]
''')
            source = root / "sources/terminal"
            (source / "src").mkdir(parents=True)
            (source / "Cargo.toml").write_text('[package]\nname="terminal"\nversion="1.0.0"\n')
            (source / "Cargo.lock").write_text('version=4\n')
            (source / "src/main.rs").write_text("fn main() {}")
            self.assertEqual(list(apps.catalog(root)), ["new-terminal"])
            first = apps.prepare(root)
            self.assertEqual(set(first["sources"]), {"terminal"})
            (source / "src/main.rs").write_text("fn main() { println!(\"new\"); }")
            self.assertNotEqual(first["identity"], apps.prepare(root)["identity"])
            self.assertEqual(development.restart_policy(apps.catalog(root), ["new-terminal"])["reopen"], ["new-terminal"])

    def test_invalid_source_inputs_cannot_escape_the_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source-projects.toml").write_text('schema=1\n[projects.app]\ninputs=["Cargo.toml","Cargo.lock","../private"]\n')
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                source_catalog(root)

    def test_fingerprints_ignore_timestamps_but_detect_deleted_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source").write_text("code")
            before = development.content_identity([root])
            (root / "source").touch()
            self.assertEqual(before, development.content_identity([root]))
            (root / "source").unlink()
            self.assertNotEqual(before, development.content_identity([root]))


class BuildIdentityTests(unittest.TestCase):
    def test_real_planner_tracks_declared_dependencies_and_checks_cached_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sysroot = root / "out/sysroot"
            tools = root / "out/bootstrap/root/tools"
            rust = root / "rust"
            for path in [sysroot / ".bootstrap-validated.json",
                         tools / "bin" / (apps.GNU_TARGET + "-gcc"), rust / "bin/rustc"]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            applications = {}
            for name in ["one", "two"]:
                recipe = root / name
                recipe.write_text(name)
                applications[name] = {"recipe": str(recipe), "package": {"version": "1.0", "revision": 1},
                                      "source": {"projects": ["framework", name]}}
            snapshot = {"sources": {name: {"identity": name} for name in ["framework", "one", "two"]},
                        "overlays": []}
            manifest = root / "out/apps/sources.json"
            manifest.parent.mkdir(parents=True)
            def plan(name):
                manifest.write_text(json.dumps(snapshot))
                return apps.build_app(root, name, sysroot, plan_only=True, development=True)
            def rust_output(command, **kwargs):
                return str(rust) if "--print" in command else "fixture rustc"
            with patch.object(apps, "catalog", return_value=applications), \
                 patch.object(apps, "preflight", return_value={"ready_to_build": True, "applications": [{"libraries": {}}]}), \
                 patch.object(apps, "_verify_snapshot", return_value=root), \
                 patch.object(apps, "_sysroot_identity", return_value="sdk"), \
                 patch.object(apps.subprocess, "check_output", side_effect=rust_output):
                first = {name: plan(name) for name in applications}
                snapshot["sources"]["one"]["identity"] = "edited app"
                self.assertNotEqual(first["one"]["build_identity"], plan("one")["build_identity"])
                self.assertEqual(first["two"]["build_identity"], plan("two")["build_identity"])
                snapshot["sources"]["framework"]["identity"] = "edited framework"
                for name in applications:
                    self.assertNotEqual(first[name]["build_identity"], plan(name)["build_identity"])
                current = plan("one")
                artifact = root / "out/app.pkg.tar.xz"
                artifact.write_bytes(b"cached package")
                receipt = root / "out/apps/build/one" / current["build_identity"] / "result.json"
                receipt.parent.mkdir(parents=True)
                receipt.write_text(json.dumps({"build_identity": current["build_identity"],
                                               "path": str(artifact), "sha256": apps.sha256(artifact)}))
                self.assertTrue(plan("one")["cached"])
                self.assertEqual(apps.build_app(root, "one", sysroot, development=True)["path"], str(artifact))
                artifact.write_bytes(b"corrupted")
                self.assertFalse(plan("one")["cached"])


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vm = self.root / "out/vms/dev"
        self.vm.mkdir(parents=True)
        (self.root / "out/state").mkdir()
        (self.vm / "state.json").write_text('{"base_image_sha256":"base"}')
        (self.root / "profiles").mkdir()
        (self.root / "profiles/desktop-dev.toml").write_text('packages=["one","two"]\n')
        (self.root / "out/sdk").mkdir()
        sdk = {"identity": "sdk", "sysroot": str(self.root / "out/sysroot"),
               "packages": [{"package": "libc", "sha256": "libc-hash"}]}
        (self.root / "out/sdk/sdk.json").write_text(json.dumps(sdk))
        (self.root / "out/sdk/current.json").write_text(json.dumps({**sdk, "report": str(self.root / "out/sdk/sdk.json")}))
        self.applications = {name: {"development": {"restart": "reopen"}} for name in ["one", "two"]}
        self.installed = {"one": "old", "two": "v-two", "libc": "1-1"}
        self.guest = {"profile": "experimental-desktop-dev", "capabilities": {"development_agent": True},
                      "packages": [{"name": "libc", "version": ["1-1"], "sha256": "libc-hash"}]}
        self.calls = []
        owner = self
        class Agent:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, executable, args):
                owner.calls.append((executable, args))
                return {"out-data": json.dumps(owner.guest) if executable == "/bin/cat" else
                        "\n".join(f"{k} {v}" for k, v in owner.installed.items())}
        patches = [patch.object(development, "vm_directory", return_value=self.vm),
                   patch.object(development, "GuestAgent", Agent),
                   patch.object(development, "source_catalog", return_value={"framework": {}}),
                   patch.object(development, "base_inputs", return_value="base-inputs"),
                   patch.object(development.upstream, "synchronize", return_value={}),
                   patch.object(development.upstream, "check", return_value=[{"behind": 0}]),
                   patch.object(development.apps, "catalog", return_value=self.applications),
                   patch.object(development.apps, "prepare", return_value={"identity": "sources"}),
                   patch("distro_build.compose.runtime_packages", return_value=["libc", "one", "two"])]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        self.build_calls = []
        def build(project, name, sysroot, **kwargs):
            self.build_calls.append((name, kwargs["plan_only"] if "plan_only" in kwargs else False))
            if kwargs.get("plan_only"):
                return {"name": name, "version": "v-" + name, "build_identity": name, "cached": True}
            return {"package": name}
        self.build = patch.object(development.apps, "build_app", side_effect=build).start()
        self.addCleanup(patch.stopall)
        def install(agent, artifacts, *, restart, system_shell=False):
            for artifact in artifacts:
                self.installed[artifact["package"]] = "v-" + artifact["package"]
            return {"packages": artifacts, "desktop_restarted": restart}
        self.install = patch.object(development, "install", side_effect=install).start()

    def run_dev(self, **options):
        return development.develop(self.root, name="dev", emit=lambda _: None, **options)

    def test_only_changed_app_is_installed_and_second_run_is_a_noop(self):
        report = self.run_dev(adopt_base=True)
        self.assertEqual(self.build_calls, [("one", True), ("two", True), ("one", False)])
        self.assertFalse(report["installation"]["desktop_restarted"])
        self.assertEqual(report["restart"]["reopen"], ["one"])
        self.build_calls.clear(); self.install.reset_mock()
        self.assertEqual(self.run_dev()["installation"]["packages"], [])
        self.install.assert_not_called()

    def test_framework_update_deploys_all_consumers_in_one_transaction(self):
        self.installed["two"] = "old"
        self.applications["two"]["development"] = {"restart": "desktop"}
        report = self.run_dev(adopt_base=True)
        self.assertEqual(self.install.call_count, 1)
        self.assertTrue(report["installation"]["desktop_restarted"])
        self.assertEqual([item["package"] for item in self.install.call_args.args[1]], ["one", "two"])

    def test_system_shell_manifest_selects_its_closure_and_restart_mode(self):
        (self.root / "profiles/desktop-dev.toml").write_text('packages=["one","two"]\n[system_shell]\npackages=["one"]\n')
        self.guest["system_shell"] = True
        self.applications["one"]["development"] = {"restart": "desktop"}
        with patch("distro_build.compose.runtime_packages", return_value=["libc", "one"]) as closure:
            self.run_dev(adopt_base=True)
        closure.assert_called_once_with(self.root, ["one"], providers={"libudev": "systemd"})
        self.assertEqual(self.build_calls, [("one", True), ("one", False)])
        self.assertTrue(self.install.call_args.kwargs["system_shell"])
        self.assertTrue(self.install.call_args.kwargs["restart"])

    def test_service_restart_and_no_restart_are_manifest_driven(self):
        self.applications["one"]["development"] = {"restart": "services", "services": ["future.service"]}
        self.run_dev(adopt_base=True)
        self.assertIn(("/usr/bin/systemctl", ["--user", "--machine=custom@.host", "try-restart", "future.service"]), self.calls)
        self.calls.clear(); self.installed["one"] = "old"
        self.run_dev(restart=False)
        self.assertFalse(any(command == "/usr/bin/systemctl" for command, _ in self.calls))

    def test_plan_does_not_fetch_build_install_or_adopt(self):
        report = self.run_dev(plan_only=True, adopt_base=True)
        development.upstream.check.assert_not_called()
        development.upstream.synchronize.assert_not_called()
        self.install.assert_not_called()
        self.assertEqual(self.build_calls, [("one", True), ("two", True)])
        self.assertFalse((self.vm / "development.json").exists())
        self.assertTrue(report["success"])

    def test_behind_upstream_stops_before_snapshot_or_compilation(self):
        development.upstream.check.return_value = [{"behind": 2}]
        with self.assertRaisesRegex(RuntimeError, "integration required"):
            self.run_dev(adopt_base=True)
        self.build.assert_not_called()
        self.assertFalse((self.vm / "development.json").exists())

    def test_native_mismatch_and_base_changes_stop_before_build(self):
        self.run_dev(adopt_base=True)
        previous = (self.vm / "development.json").read_bytes()
        self.build.reset_mock()
        with patch.object(development, "base_inputs", return_value="changed"):
            with self.assertRaisesRegex(RuntimeError, "Base inputs"):
                self.run_dev(adopt_base=True)
        self.build.assert_not_called()
        self.assertEqual(previous, (self.vm / "development.json").read_bytes())
        self.guest["packages"][0]["sha256"] = "different"
        with self.assertRaisesRegex(RuntimeError, "SDK/base"):
            self.run_dev()

    def test_failed_install_does_not_advance_success_baseline(self):
        self.install.side_effect = RuntimeError("pacman rejected dependencies")
        with self.assertRaisesRegex(RuntimeError, "pacman rejected"):
            self.run_dev(adopt_base=True)
        self.assertFalse((self.vm / "development.json").exists())
        self.assertFalse(json.loads((self.vm / "development-report.json").read_text())["success"])

    def test_cli_keeps_explicit_deployment_and_adds_automatic_development(self):
        args = parser().parse_args(["dev", "--name", "future", "--plan", "--build-profile", "release"])
        self.assertTrue(args.plan)
        self.assertEqual(args.build_profile, "release")


if __name__ == "__main__":
    unittest.main()
