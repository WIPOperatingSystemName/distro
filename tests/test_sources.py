import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.model import BuildError, Source
from distro_build.sources import extract, fetch


class SourceTests(unittest.TestCase):
    def test_tampered_offline_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "source.tar.xz").write_bytes(b"modified upstream source")
            source = Source("example", "https://example.org/source.tar.xz", hashlib.sha256(b"original source").hexdigest(), "source.tar.xz")
            with self.assertRaisesRegex(BuildError, "checksum mismatch"):
                fetch(source, cache, offline=True)

    def test_traversal_and_escaping_symlinks_are_rejected(self):
        for kind in ("traversal", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "source.tar"
                with tarfile.open(archive, "w") as stream:
                    entry = tarfile.TarInfo("../escaped" if kind == "traversal" else "source/escape")
                    if kind == "symlink":
                        entry.type = tarfile.SYMTYPE
                        entry.linkname = "../../escaped"
                        stream.addfile(entry)
                    else:
                        entry.size = 6
                        stream.addfile(entry, io.BytesIO(b"unsafe"))
                with self.assertRaises(BuildError):
                    extract(archive, root / "extract")
                self.assertFalse((root / "escaped").exists())

    def test_safe_source_tree_is_extracted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar"
            with tarfile.open(archive, "w") as stream:
                entry = tarfile.TarInfo("source/hello.c")
                entry.size = 5
                stream.addfile(entry, io.BytesIO(b"hello"))
            source = extract(archive, root / "extract")
            self.assertEqual((source / "hello.c").read_bytes(), b"hello")


if __name__ == "__main__":
    unittest.main()
