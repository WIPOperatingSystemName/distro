"""Reject unsupported executable build roles before catalog/build execution."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.graph import plan
from distro_build.model import BuildError, Recipe, catalog

class RecipeRoleTests(unittest.TestCase):
    def test_catalog_rejects_named_native_packages_instead_of_cross_target_generators(self):
        source = (Path(__file__).resolve().parents[1] / "packages/curl/package.toml").read_text()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            package = project / "packages/curl/package.toml"
            package.parent.mkdir(parents=True)
            package.write_text(source.replace("native = []", 'native = ["schema-generator"]'))
            with self.assertRaisesRegex(BuildError, "named native package namespace is not implemented"):
                catalog(project)
            package.write_text(source)
            self.assertEqual(catalog(project)["curl"].dependencies["native"], ())

    def test_abstract_role_graph_still_orders_native_edges(self):
        generator = Recipe(Path("generator"), {"name": "generator"}, (), {}, {})
        consumer = Recipe(Path("consumer"), {"name": "consumer"}, (), {}, {"native": ("generator",)})
        self.assertEqual([recipe.name for recipe in plan({"generator": generator, "consumer": consumer}, ["consumer"])],
                         ["generator", "consumer"])

if __name__ == "__main__":
    unittest.main()
