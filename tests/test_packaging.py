from pathlib import Path
import gzip
import io
import os
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.packaging import PackageError, export_package, inspect_package


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        (self.stage / "usr/bin").mkdir(parents=True)
        (self.stage / "etc").mkdir()
        (self.stage / "usr/bin/example").write_text("#!/bin/sh\necho example\n")
        (self.stage / "usr/bin/example").chmod(0o755)
        (self.stage / "etc/example.conf").write_text("default=one\n")
        (self.stage / "usr/bin/alias").symlink_to("example")
        self.metadata = {"name": "example", "version": "1.0", "revision": 1,
                         "arch": "x86_64", "description": "Example tool",
                         "licenses": ["MIT"], "backup": ["etc/example.conf"]}

    def tearDown(self):
        self.temp.cleanup()

    def export(self, **kwargs):
        return export_package(self.stage, self.root / "packages", self.metadata,
                              source_date_epoch=1234567890, **kwargs)

    def rewrite(self, artifact, *, extra=None, change=None):
        path = self.root / "modified.pkg.tar.xz"
        with tarfile.open(artifact.path) as original, tarfile.open(path, "w:xz") as output:
            for member in original:
                payload = original.extractfile(member).read() if member.isfile() else None
                if change:
                    member, payload = change(member, payload)
                if payload is not None:
                    member.size = len(payload)
                output.addfile(member, io.BytesIO(payload) if payload is not None else None)
            if extra:
                output.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
        return path

    def test_reproducible_archive_and_root_owned_payload(self):
        first = self.export()
        os.utime(self.stage / "usr/bin/example", (42, 42))
        second = self.export()
        self.assertEqual(first.sha256, second.sha256)
        info = inspect_package(first.path)
        self.assertEqual(info["metadata"]["backup"], ["etc/example.conf"])
        self.assertTrue(all((item["uid"], item["gid"]) == (0, 0) for item in info["files"]))
        command = next(item for item in info["files"] if item["path"] == "usr/bin/example")
        self.assertEqual(command["mode"], 0o755)

    def test_changed_payload_requires_new_revision(self):
        original = self.export()
        (self.stage / "usr/bin/example").write_text("changed\n")
        with self.assertRaisesRegex(PackageError, "refusing to overwrite"):
            self.export()
        self.metadata["revision"] = 2
        changed = self.export()
        self.assertNotEqual(original.path, changed.path)

    def test_owned_account_ids_are_not_builder_ids(self):
        info = inspect_package(self.export(ownership={"etc/example.conf": (88, 89)}).path)
        config = next(item for item in info["files"] if item["path"] == "etc/example.conf")
        self.assertEqual((config["uid"], config["gid"]), (88, 89))

    def test_backup_must_be_owned_regular_file(self):
        for backup in ("/etc/example.conf", "usr/bin/alias", "etc/missing"):
            self.metadata["backup"] = [backup]
            with self.assertRaises(PackageError):
                self.export()

    def test_hostile_archive_paths_are_rejected_without_extraction(self):
        original = self.export()
        for name in ("../escape", "/tmp/escape", "usr/../../escape", "usr\\..\\escape", "usr/line\nname", r"usr/bad\xZZ"):
            member = tarfile.TarInfo(name)
            member.size = 1
            with self.assertRaises(PackageError):
                inspect_package(self.rewrite(original, extra=member))
        self.assertFalse((self.root / "escape").exists())

    def test_duplicate_payload_is_rejected(self):
        member = tarfile.TarInfo("etc/example.conf")
        member.size = 1
        with self.assertRaisesRegex(PackageError, "duplicate archive"):
            inspect_package(self.rewrite(self.export(), extra=member))

    def test_payload_below_late_symlink_is_rejected(self):
        def replace(member, payload):
            if member.name == "usr":
                member.type, member.linkname = tarfile.SYMTYPE, "/tmp"
            return member, payload
        with self.assertRaisesRegex(PackageError, "non-directory"):
            inspect_package(self.rewrite(self.export(), change=replace))

    def test_payload_change_fails_manifest_integrity(self):
        def change(member, payload):
            if member.name == "etc/example.conf":
                payload = b"malicious\n"
            return member, payload
        with self.assertRaisesRegex(PackageError, "MTREE payload"):
            inspect_package(self.rewrite(self.export(), change=change))

    def test_metadata_injection_and_reserved_stage_files_are_rejected(self):
        self.metadata["description"] = "description\ndepend = malicious"
        with self.assertRaises(PackageError):
            self.export()
        self.metadata["description"] = "safe"
        (self.stage / ".PKGINFO").write_text("forged")
        with self.assertRaisesRegex(PackageError, "reserved"):
            self.export()

    def test_symlink_cannot_escape_installed_root(self):
        (self.stage / "bad").symlink_to("../outside")
        with self.assertRaisesRegex(PackageError, "symlink escapes"):
            self.export()

    def test_mtree_escaped_names_roundtrip(self):
        (self.stage / "usr/bin/a b#é").write_text("content\n")
        info = inspect_package(self.export().path)
        self.assertIn("usr/bin/a b#é", [item["path"] for item in info["files"]])

    def test_systemd_literal_filename_escapes_roundtrip(self):
        units = self.stage / "usr/lib/systemd/system"
        units.mkdir(parents=True)
        name = r"system-systemd\x2dmute\x2dconsole.slice"
        (units / name).write_text("[Slice]\n")
        (units / "alias.slice").symlink_to(name)
        info = inspect_package(self.export().path)
        entries = {item["path"]: item for item in info["files"]}
        self.assertIn("usr/lib/systemd/system/" + name, entries)
        self.assertEqual(entries["usr/lib/systemd/system/alias.slice"]["link"], name)


if __name__ == "__main__":
    unittest.main()
