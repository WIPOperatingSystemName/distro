from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.graph import affected, plan
from distro_build.model import BuildError, Recipe


def recipe(name, dependencies=()):
    return Recipe(Path(name), {"name": name}, (), {}, {"target": tuple(dependencies)})


class GraphTests(unittest.TestCase):
    def test_shared_library_change_rebuilds_transitive_consumers(self):
        recipes = {r.name: r for r in (recipe("libc"), recipe("audio", ("libc",)), recipe("shell", ("audio",)), recipe("unrelated"))}
        self.assertEqual(affected(recipes, {"libc"}), {"libc", "audio", "shell"})
        self.assertEqual([r.name for r in plan(recipes, ["shell"])], ["libc", "audio", "shell"])

    def test_cycle_reports_dependency_chain(self):
        recipes = {"a": recipe("a", ("b",)), "b": recipe("b", ("a",))}
        with self.assertRaisesRegex(BuildError, "a -> b -> a"):
            plan(recipes, ["a"])

    def test_missing_dependency_is_not_silently_omitted(self):
        with self.assertRaisesRegex(BuildError, "unknown package missing"):
            plan({"shell": recipe("shell", ("missing",))}, ["shell"])

    def test_runtime_cycle_is_included_without_becoming_a_compile_cycle(self):
        systemd = Recipe(Path("systemd"), {"name": "systemd"}, (), {}, {"runtime": ("dbus",)})
        dbus = recipe("dbus", ("systemd",))
        recipes = {r.name: r for r in (systemd, dbus)}
        self.assertEqual([r.name for r in plan(recipes, ["dbus"])], ["systemd", "dbus"])
        self.assertEqual([r.name for r in plan(recipes, ["systemd"], include_runtime=False)], ["systemd"])

    def test_missing_runtime_package_remains_an_error(self):
        shell = Recipe(Path("shell"), {"name": "shell"}, (), {}, {"runtime": ("missing",)})
        with self.assertRaisesRegex(BuildError, "unknown package missing"):
            plan({"shell": shell}, ["shell"])


if __name__ == "__main__":
    unittest.main()
