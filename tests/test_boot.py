import gzip
from pathlib import Path
import struct
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.boot import _snapshot, boot_config, build_initramfs, validate_efi


def read_newc(data):
    offset, result = 0, {}
    while True:
        header = data[offset:offset + 110]
        if len(header) != 110 or header[:6] != b"070701":
            raise AssertionError("invalid newc header")
        fields = [int(header[start:start + 8], 16) for start in range(6, 110, 8)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        start = offset + 110
        name = data[start:start + namesize - 1].decode()
        start = (start + namesize + 3) & ~3
        content = data[start:start + size]
        offset = (start + size + 3) & ~3
        if name == "TRAILER!!!":
            return result
        result[name] = {"mode": mode, "uid": fields[2], "gid": fields[3], "content": content}


class BootTests(unittest.TestCase):
    def test_snapshot_excludes_submodule_git_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / ".git").write_text("gitdir: ../../.git/modules/bootloader\n")
            (source / "Cargo.toml").write_text("[workspace]\n")
            snapshot = root / "snapshot"
            record = _snapshot(source, snapshot)
            self.assertFalse((snapshot / ".git").exists())
            self.assertEqual([item["path"] for item in record["files"]], ["Cargo.toml"])

    def test_initramfs_preserves_execution_and_symlinks_with_root_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            (root / "bin").mkdir(parents=True)
            (root / "init").write_text("#!/bin/sh\n")
            (root / "init").chmod(0o755)
            (root / "bin/busybox").write_bytes(b"source-built-program")
            (root / "bin/busybox").chmod(0o755)
            (root / "bin/sh").symlink_to("busybox")
            first = build_initramfs(root, base / "first.img")
            second = build_initramfs(root, base / "second.img")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            records = read_newc(gzip.decompress(first.read_bytes()))
            self.assertEqual(records["bin/sh"]["content"], b"busybox")
            self.assertEqual(records["init"]["mode"] & 0o777, 0o755)
            self.assertEqual(records["bin/busybox"]["content"], b"source-built-program")
            self.assertTrue(all(member["uid"] == member["gid"] == 0 for member in records.values()))

    def test_initramfs_cannot_pack_itself_or_special_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "init").write_text("#!/bin/sh\n")
            (root / "init").chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "outside"):
                build_initramfs(root, root / "initrd.img")
            output = root.parent / (root.name + ".link")
            output.symlink_to(root.parent / (root.name + ".foreign"))
            try:
                with self.assertRaisesRegex(RuntimeError, "ordinary"):
                    build_initramfs(root, output)
            finally:
                output.unlink()
            import os
            os.mkfifo(root / "unsafe-fifo")
            with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                build_initramfs(root, root.parent / (root.name + ".img"))

    def test_config_boots_only_custom_source_payload(self):
        config = tomllib.loads(boot_config('Custom Distro "test"'))
        self.assertEqual(config["selection"], "auto")
        self.assertEqual(config["targets"][0]["name"], 'Custom Distro "test"')
        self.assertEqual(config["targets"][0]["image"], r"\EFI\Custom\kernel.efi")
        self.assertIn(r"initrd=\EFI\Custom\initrd.img", config["targets"][0]["arguments"])
        uki = tomllib.loads(boot_config(uki=True))
        self.assertNotIn("arguments", uki["targets"][0])

    def test_pe_validation_rejects_wrong_architecture_and_header_offset(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "kernel.efi"
            data = bytearray(256)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 0x3C, 64)
            data[64:68] = b"PE\0\0"
            struct.pack_into("<H", data, 68, 0x8664)
            struct.pack_into("<H", data, 88, 0x20B)
            struct.pack_into("<H", data, 156, 10)
            path.write_bytes(data)
            self.assertEqual(validate_efi(path)["size"], len(data))
            struct.pack_into("<H", data, 68, 0xAA64)
            path.write_bytes(data)
            with self.assertRaises(RuntimeError):
                validate_efi(path)
            struct.pack_into("<I", data, 0x3C, 0xFFFFFF00)
            path.write_bytes(data)
            with self.assertRaises(RuntimeError):
                validate_efi(path)


if __name__ == "__main__":
    unittest.main()
