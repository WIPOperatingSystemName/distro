from pathlib import Path
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distro_build.media import build_disk, validate_disk


def fat_reader(path, start):
    stream = path.open("rb")
    stream.seek(start * 512)
    header = stream.read(512)
    reserved = struct.unpack_from("<H", header, 14)[0]
    fat_sectors = struct.unpack_from("<I", header, 36)[0]
    fats = header[16]
    cluster_sectors = header[13]
    data_start = start + reserved + fats * fat_sectors

    def cluster_chain(first):
        content = bytearray()
        seen = set()
        while 2 <= first < 0x0FFFFFF8:
            if first in seen:
                raise AssertionError("cyclic FAT chain")
            seen.add(first)
            stream.seek((data_start + (first - 2) * cluster_sectors) * 512)
            content.extend(stream.read(cluster_sectors * 512))
            stream.seek((start + reserved) * 512 + first * 4)
            first = struct.unpack("<I", stream.read(4))[0] & 0x0FFFFFFF
        return bytes(content)

    def directory(first, prefix=""):
        content = cluster_chain(first)
        result, long_name = {}, {}
        for offset in range(0, len(content), 32):
            entry = content[offset:offset + 32]
            if entry[0] == 0:
                break
            if entry[11] == 0x0F:
                order = entry[0] & 31
                units = [struct.unpack_from("<H", entry, i)[0] for i in (1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30)]
                long_name[order] = units
                continue
            if long_name:
                units = [u for index in sorted(long_name) for u in long_name[index]]
                units = units[:units.index(0)] if 0 in units else units
                name = struct.pack(f"<{len(units)}H", *units).decode("utf-16-le")
                long_name = {}
            else:
                base, ext = entry[:8].decode().strip(), entry[8:11].decode().strip()
                name = base + ("." + ext if ext else "")
            if name in {".", ".."}:
                continue
            child = (struct.unpack_from("<H", entry, 20)[0] << 16) | struct.unpack_from("<H", entry, 26)[0]
            size = struct.unpack_from("<I", entry, 28)[0]
            if entry[11] & 0x10:
                result.update(directory(child, prefix + name + "/"))
            else:
                result[prefix + name] = cluster_chain(child)[:size] if child else b""
        return result

    try:
        return directory(struct.unpack_from("<I", header, 44)[0])
    finally:
        stream.close()


class MediaTests(unittest.TestCase):
    def make_esp(self, base):
        esp = base / "esp"
        (esp / "EFI/BOOT").mkdir(parents=True)
        (esp / "EFI/Telorgon").mkdir()
        (esp / "EFI/BOOT/BOOTX64.EFI").write_bytes(b"loader" * 311)
        (esp / "EFI/Telorgon/boot.toml").write_text('name = "Custom Distro"\n')
        (esp / "a long Unicode name ☃.txt").write_bytes(b"long-name-data")
        (esp / "empty.txt").touch()
        return esp

    def test_gpt_and_fat_roundtrip_multicluster_files_and_long_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            esp = self.make_esp(base)
            disk = base / "disk.img"
            build_disk(esp, None, disk, size_mib=72, esp_mib=64)
            geometry = validate_disk(disk)
            self.assertEqual(len(geometry["partitions"]), 1)
            files = fat_reader(disk, geometry["partitions"][0]["first"])
            self.assertEqual(files["EFI/BOOT/BOOTX64.EFI"], b"loader" * 311)
            self.assertEqual(files["EFI/TELORGON/boot.toml"], b'name = "Custom Distro"\n')
            self.assertEqual(files["a long Unicode name ☃.txt"], b"long-name-data")
            self.assertEqual(files["EMPTY.TXT"], b"")
            with disk.open("r+b") as stream:
                stream.seek(512 + 16)
                stream.write(b"BAD!")
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                validate_disk(disk)

    def test_inputs_cannot_escape_through_symlinks_and_case_collisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            esp = self.make_esp(base)
            (esp / "unsafe").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(RuntimeError, "symlinks"):
                build_disk(esp, None, base / "disk.img", size_mib=72, esp_mib=64)
            (esp / "unsafe").unlink()
            (esp / "case").touch()
            (esp / "CASE").touch()
            with self.assertRaisesRegex(RuntimeError, "collision"):
                build_disk(esp, None, base / "disk.img", size_mib=72, esp_mib=64)
            output = base / "output-link"
            output.symlink_to(base / "other")
            with self.assertRaisesRegex(RuntimeError, "ordinary file"):
                build_disk(esp, None, output, size_mib=72, esp_mib=64)

    @unittest.skipUnless(shutil.which("fakeroot") and shutil.which("mkfs.ext4") and shutil.which("debugfs"),
                         "ext4 metadata check needs host media seed tools")
    def test_ext4_records_root_ownership_without_changing_source_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            esp = self.make_esp(base)
            root = base / "root"
            (root / "etc").mkdir(parents=True)
            owned = root / "etc/owned"
            owned.write_bytes(b"distro-owned-content")
            (root / "home/custom/config").mkdir(parents=True)
            personal = root / "home/custom/config/preferences"
            personal.write_text("guest preferences\n")
            ownership = owned.stat().st_uid, owned.stat().st_gid
            personal_ownership = personal.stat().st_uid, personal.stat().st_gid
            disk = base / "disk.img"
            build_disk(esp, root, disk, size_mib=96, esp_mib=64, ownership={"home/custom": (1000, 1000)})
            partition = validate_disk(disk)["partitions"][1]
            extracted = base / "root.ext4"
            with disk.open("rb") as source, extracted.open("wb") as target:
                source.seek(partition["first"] * 512)
                remaining = (partition["last"] - partition["first"] + 1) * 512
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    target.write(chunk)
                    remaining -= len(chunk)
            result = subprocess.run([shutil.which("debugfs"), "-R", "stat /etc/owned", str(extracted)],
                                    capture_output=True, text=True, check=True)
            self.assertRegex(result.stdout, r"User:\s+0\s+Group:\s+0")
            self.assertEqual((owned.stat().st_uid, owned.stat().st_gid), ownership)
            for path in ("/home/custom", "/home/custom/config/preferences"):
                result = subprocess.run([shutil.which("debugfs"), "-R", "stat " + path, str(extracted)],
                                        capture_output=True, text=True, check=True)
                self.assertRegex(result.stdout, r"User:\s+1000\s+Group:\s+1000")
            self.assertEqual((personal.stat().st_uid, personal.stat().st_gid), personal_ownership)


if __name__ == "__main__":
    unittest.main()
