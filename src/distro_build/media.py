"""Construct GPT/FAT32 disk images as ordinary files, without mounts or root."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import uuid
import zlib
from typing import Mapping

from .boot import sha256


SECTOR = 512
ALIGNMENT = 2048
ESP_TYPE = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
LINUX_TYPE = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")
NAMESPACE = uuid.UUID("e1f32c4d-bc0e-4e67-936f-e8da9f5d60e3")


@dataclass
class _Node:
    name: str
    directory: bool
    path: Path | None = None
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)
    alias: bytes = b""
    long_name: bool = False
    first: int = 0
    clusters: int = 0
    size: int = 0


def _valid_name(name: str) -> None:
    if (not name or name in {".", ".."} or name.endswith((" ", "."))
            or any(ord(c) < 32 or c in '\\/:*?"<>|' for c in name)):
        raise RuntimeError(f"Invalid FAT filename: {name!r}")
    try:
        encoded = name.encode("utf-16-le")
    except UnicodeEncodeError as error:
        raise RuntimeError(f"FAT filename is not valid Unicode: {name!r}") from error
    if len(encoded) > 510:
        raise RuntimeError("FAT filename exceeds 255 UTF-16 units")


def _aliases(children: list[_Node]) -> None:
    used = set()
    names = set()
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~"
    for child in children:
        _valid_name(child.name)
        folded = child.name.casefold()
        if folded in names:
            raise RuntimeError(f"FAT filename collision: {child.name}")
        names.add(folded)
        base, dot, extension = child.name.rpartition(".")
        if not dot:
            base, extension = child.name, ""
        upper_base, upper_extension = base.upper(), extension.upper()
        simple = (1 <= len(upper_base) <= 8 and len(upper_extension) <= 3
                  and all(c in allowed for c in upper_base + upper_extension))
        alias = (upper_base.ljust(8) + upper_extension.ljust(3)).encode("ascii") if simple else None
        if alias is None or alias in used:
            cleaned = "".join(c for c in upper_base if c in allowed) or "FILE"
            suffix = "".join(c for c in upper_extension if c in allowed)[:3]
            number = 1
            while True:
                tail = f"~{number}"
                candidate = cleaned[:8 - len(tail)] + tail
                alias = (candidate.ljust(8) + suffix.ljust(3)).encode("ascii")
                if alias not in used:
                    break
                number += 1
                if number > 999999:
                    raise RuntimeError("FAT short-name namespace exhausted")
            child.long_name = True
        else:
            # Case-insensitive firmware lookups work with conventional uppercase 8.3 names.
            child.long_name = False
        child.alias = alias
        used.add(alias)


def _tree(source: Path) -> _Node:
    source = source.resolve(strict=True)
    root = _Node("", True, source)

    def visit(parent: _Node) -> None:
        for path in sorted(parent.path.iterdir(), key=lambda p: p.name):
            if path.is_symlink():
                raise RuntimeError(f"ESP symlinks are unsupported: {path}")
            info = path.stat()
            if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                raise RuntimeError(f"Unsupported ESP file type: {path}")
            node = _Node(path.name, stat.S_ISDIR(info.st_mode), path, parent)
            node.size = 0 if node.directory else info.st_size
            if node.size > 0xFFFFFFFF:
                raise RuntimeError(f"File exceeds FAT32's size limit: {path}")
            parent.children.append(node)
            if node.directory:
                visit(node)
        _aliases(parent.children)

    visit(root)
    return root


def _long_entries(node: _Node) -> bytes:
    if not node.long_name:
        return b""
    units = list(struct.unpack(f"<{len(node.name.encode('utf-16-le')) // 2}H", node.name.encode("utf-16-le")))
    units.append(0)
    units.extend([0xFFFF] * (-len(units) % 13))
    checksum = 0
    for value in node.alias:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + value) & 0xFF
    result = bytearray()
    count = len(units) // 13
    for index in range(count, 0, -1):
        entry = bytearray(32)
        entry[0] = index | (0x40 if index == count else 0)
        entry[11], entry[13] = 0x0F, checksum
        chunk = units[(index - 1) * 13:index * 13]
        for offset, value in zip((1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30), chunk):
            struct.pack_into("<H", entry, offset, value)
        result.extend(entry)
    return bytes(result)


def _short_entry(alias: bytes, directory: bool, first: int, size: int = 0) -> bytes:
    entry = bytearray(32)
    entry[:11], entry[11] = alias, 0x10 if directory else 0x20
    # Fixed FAT timestamp: 1980-01-01, midnight. Build wall-clock time is not an input.
    for offset in (16, 18, 24):
        struct.pack_into("<H", entry, offset, 33)
    struct.pack_into("<H", entry, 20, first >> 16)
    struct.pack_into("<H", entry, 26, first & 0xFFFF)
    struct.pack_into("<I", entry, 28, size)
    return bytes(entry)


def _directory_bytes(node: _Node) -> bytes:
    result = bytearray()
    if node.parent is not None:
        result.extend(_short_entry(b".          ", True, node.first))
        parent_cluster = 0 if node.parent.parent is None else node.parent.first
        result.extend(_short_entry(b"..         ", True, parent_cluster))
    for child in node.children:
        result.extend(_long_entries(child))
        result.extend(_short_entry(child.alias, child.directory, child.first, child.size))
    result.extend(b"\0" * 32)
    return bytes(result)


def _nodes(root: _Node):
    yield root
    for child in root.children:
        yield from _nodes(child) if child.directory else (child,)


def write_fat32(stream, source: Path, *, start_sector: int, sectors: int,
                volume_id: int = 0x43555354) -> dict:
    """Write a self-contained FAT32 filesystem into a bounded image partition."""
    if sectors < 131072:
        raise RuntimeError("FAT32 ESP must be at least 64 MiB")
    if start_sector < 0 or sectors > 0xFFFFFFFF:
        raise RuntimeError("FAT32 sector bounds are invalid")
    root = _tree(Path(source))
    reserved, fat_count, cluster_sectors = 32, 2, 1
    fat_sectors = 1
    while True:
        clusters = (sectors - reserved - fat_count * fat_sectors) // cluster_sectors
        required = math.ceil((clusters + 2) * 4 / SECTOR)
        if required == fat_sectors:
            break
        # Fixed point can oscillate by one sector; retaining the larger FAT is valid.
        if required < fat_sectors:
            break
        fat_sectors = required
    clusters = (sectors - reserved - fat_count * fat_sectors) // cluster_sectors
    if not 65525 <= clusters < 0x0FFFFFF5:
        raise RuntimeError("Partition does not have a valid FAT32 cluster count")
    nodes = list(_nodes(root))
    next_cluster = 2
    for node in nodes:
        count = math.ceil((len(_directory_bytes(node)) if node.directory else node.size) / SECTOR)
        node.clusters = max(1, count) if node.directory else count
        node.first = next_cluster if node.clusters else 0
        next_cluster += node.clusters
    if next_cluster - 2 > clusters:
        raise RuntimeError("ESP contents exceed the FAT32 partition capacity")
    fat = bytearray(fat_sectors * SECTOR)
    struct.pack_into("<II", fat, 0, 0x0FFFFFF8, 0xFFFFFFFF)
    for node in nodes:
        for cluster in range(node.first, node.first + node.clusters):
            if node.clusters:
                following = cluster + 1 if cluster + 1 < node.first + node.clusters else 0x0FFFFFFF
                struct.pack_into("<I", fat, cluster * 4, following)
    boot = bytearray(SECTOR)
    boot[:3], boot[3:11] = b"\xeb\x58\x90", b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHII", boot, 11, SECTOR, 1, reserved, 2, 0, 0, 0xF8, 0,
                     63, 255, start_sector, sectors)
    struct.pack_into("<IHHIHH", boot, 36, fat_sectors, 0, 0, 2, 1, 6)
    boot[64], boot[66] = 0x80, 0x29
    struct.pack_into("<I", boot, 67, volume_id)
    boot[71:82], boot[82:90], boot[510:512] = b"CUSTOM EFI ", b"FAT32   ", b"\x55\xaa"
    info = bytearray(SECTOR)
    struct.pack_into("<I", info, 0, 0x41615252)
    struct.pack_into("<III", info, 484, 0x61417272, clusters - (next_cluster - 2), next_cluster)
    struct.pack_into("<I", info, 508, 0xAA550000)

    def write_sector(relative: int, content: bytes) -> None:
        stream.seek((start_sector + relative) * SECTOR)
        stream.write(content)

    write_sector(0, boot)
    write_sector(1, info)
    write_sector(6, boot)
    write_sector(7, info)
    for index in range(fat_count):
        write_sector(reserved + index * fat_sectors, fat)
    data_start = reserved + fat_count * fat_sectors
    files = []
    for node in nodes:
        if not node.clusters:
            continue
        offset = (start_sector + data_start + (node.first - 2)) * SECTOR
        stream.seek(offset)
        if node.directory:
            data = _directory_bytes(node)
            stream.write(data)
            stream.write(b"\0" * (node.clusters * SECTOR - len(data)))
        else:
            with node.path.open("rb") as source_stream:
                shutil.copyfileobj(source_stream, stream, length=1024 * 1024)
            stream.write(b"\0" * (-node.size % SECTOR))
            files.append({"path": node.path.relative_to(root.path).as_posix(), "size": node.size,
                          "sha256": sha256(node.path)})
    return {"kind": "fat32", "start_sector": start_sector, "sectors": sectors,
            "clusters": clusters, "allocated_clusters": next_cluster - 2, "files": files}


def _partition(kind: uuid.UUID, identity: uuid.UUID, first: int, last: int, name: str) -> bytes:
    encoded = name.encode("utf-16-le")
    if len(encoded) > 72:
        raise RuntimeError("GPT partition label exceeds 36 UTF-16 units")
    return struct.pack("<16s16sQQQ72s", kind.bytes_le, identity.bytes_le, first, last, 0, encoded)


def _gpt_header(current: int, backup: int, sectors: int, identity: uuid.UUID,
                entries_sector: int, entries_crc: int) -> bytes:
    header = bytearray(struct.pack("<8sIIIIQQQQ16sQIII", b"EFI PART", 0x10000, 92, 0, 0,
                                  current, backup, 34, sectors - 34, identity.bytes_le,
                                  entries_sector, 128, 128, entries_crc))
    struct.pack_into("<I", header, 16, zlib.crc32(header))
    return bytes(header).ljust(SECTOR, b"\0")


def _source_identity(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        info = path.lstat()
        digest.update(os.fsencode(path.relative_to(root).as_posix()) + b"\0")
        digest.update(str(info.st_mode).encode() + b"\0")
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(bytes.fromhex(sha256(path)))
        elif not path.is_dir():
            raise RuntimeError(f"Special files cannot enter an image root: {path}")
    return digest.hexdigest()


def root_uuid(root: Path) -> str:
    """Choose a stable prototype UUID before boot configuration is generated."""
    return str(uuid.uuid5(NAMESPACE, "root:" + _source_identity(Path(root).resolve(strict=True))))


def build_disk(esp: Path, root: Path | None, output: Path, *, size_mib: int = 512,
               esp_mib: int = 128, root_uuid: str | None = None,
               ownership: Mapping[str, tuple[int, int]] | None = None) -> dict:
    """Assemble a sparse GPT disk; optional ext4 root is populated with mkfs -d."""
    esp, output = Path(esp).resolve(strict=True), Path(output).absolute()
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise RuntimeError("Disk output must be an ordinary file, never a device or symlink")
    if not esp.is_dir() or not (esp / "EFI/BOOT/BOOTX64.EFI").is_file():
        raise RuntimeError("ESP needs the default Telorgon EFI application")
    if output.resolve().is_relative_to(esp):
        raise RuntimeError("Disk output must be outside its ESP inputs")
    if not isinstance(size_mib, int) or not isinstance(esp_mib, int) or not 64 <= esp_mib < size_mib - 4:
        raise RuntimeError("Disk needs an ESP of at least 64 MiB and partition-table space")
    if size_mib > 32768:
        raise RuntimeError("Prototype images are limited to 32 GiB")
    root = Path(root).resolve(strict=True) if root is not None else None
    if root is not None and (not root.is_dir() or output.resolve().is_relative_to(root)):
        raise RuntimeError("Image root must be a directory outside the output")
    policy = dict(ownership or {})
    if policy and root is None:
        raise RuntimeError("Numeric ownership requires a persistent root")
    owned_paths = {}
    for relative, owner in sorted(policy.items(), key=lambda item: (item[0].count("/"), item[0])):
        # Declared OS account directories use this restricted filename grammar;
        # it also prevents injecting commands into debugfs's batch interface.
        if (not relative or relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/"))
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_-." for c in relative)
                or len(owner) != 2 or any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF for value in owner)):
            raise RuntimeError("Invalid numeric image ownership policy")
        path = root / relative
        if not path.is_dir() or path.is_symlink():
            raise RuntimeError(f"Owned directory must exist in image root: {relative}")
        for child in [path, *sorted(path.rglob("*"))]:
            entry = child.relative_to(root).as_posix()
            if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_-." for c in entry):
                raise RuntimeError(f"Unsupported filename in owned image directory: {entry}")
            owned_paths[entry] = tuple(owner)
    output.parent.mkdir(parents=True, exist_ok=True)
    root_identity = _source_identity(root) if root else "ram-only"
    esp_identity = _source_identity(esp)
    identity = uuid.uuid5(NAMESPACE, esp_identity + root_identity + json.dumps(policy, sort_keys=True))
    root_id = uuid.UUID(root_uuid) if root_uuid else uuid.uuid5(NAMESPACE, "root:" + root_identity)
    sectors = size_mib * 2048
    esp_start, esp_sectors = ALIGNMENT, esp_mib * 2048
    root_start = esp_start + esp_sectors
    root_end = sectors - 34
    if root is not None and root_end - root_start + 1 < 32768:
        raise RuntimeError("Disk root partition is too small")
    entries = bytearray(128 * 128)
    entries[:128] = _partition(ESP_TYPE, uuid.uuid5(identity, "esp"), esp_start,
                               esp_start + esp_sectors - 1, "Custom Distro EFI")
    if root is not None:
        entries[128:256] = _partition(LINUX_TYPE, uuid.uuid5(identity, "root-partition"),
                                     root_start, root_end, "Custom Distro Root")
    entries_crc = zlib.crc32(entries)
    temporary = Path(tempfile.mkdtemp(prefix="media-", dir=output.parent))
    staged = temporary / "disk.img"
    try:
        with staged.open("w+b") as stream:
            stream.truncate(sectors * SECTOR)
            mbr = bytearray(SECTOR)
            mbr[446:462] = struct.pack("<B3sB3sII", 0, b"\0\2\0", 0xEE, b"\xff\xff\xff", 1,
                                        min(sectors - 1, 0xFFFFFFFF))
            mbr[510:512] = b"\x55\xaa"
            stream.write(mbr)
            stream.seek(SECTOR)
            stream.write(_gpt_header(1, sectors - 1, sectors, identity, 2, entries_crc))
            stream.seek(2 * SECTOR)
            stream.write(entries)
            stream.seek((sectors - 33) * SECTOR)
            stream.write(entries)
            stream.seek((sectors - 1) * SECTOR)
            stream.write(_gpt_header(sectors - 1, 1, sectors, identity, sectors - 33, entries_crc))
            fat = write_fat32(stream, esp, start_sector=esp_start, sectors=esp_sectors,
                              volume_id=identity.int & 0xFFFFFFFF)
            if root is not None:
                mkfs = shutil.which("mkfs.ext4")
                if not mkfs:
                    raise RuntimeError("mkfs.ext4 is required to populate the private root image")
                root_image = temporary / "root.ext4"
                with root_image.open("wb") as target:
                    target.truncate((root_end - root_start + 1) * SECTOR)
                command = [mkfs, "-q", "-F", "-U", str(root_id), "-L", "CUSTOM_ROOT", "-d", str(root),
                           "-E", "lazy_itable_init=0,lazy_journal_init=0,root_owner=0:0", str(root_image)]
                fakeroot = shutil.which("fakeroot")
                if not fakeroot:
                    raise RuntimeError("fakeroot is required to record root-owned prototype files")
                # An earlier ALPM fakeroot process does not preserve its ownership
                # database. A fresh fakeroot's default unknown-is-root stat policy
                # presents this console slice as root-owned directly to mkfs.
                # No chown syscall, real or simulated, touches the input tree.
                fake_command = [fakeroot, *command]
                env = os.environ.copy()
                env["TMPDIR"] = str(temporary)
                process = subprocess.run(fake_command, capture_output=True, text=True, check=False, env=env)
                if process.returncode:
                    raise RuntimeError(f"Root image creation failed: {process.stderr.strip()}")
                if owned_paths:
                    debugfs = shutil.which("debugfs")
                    if not debugfs:
                        raise RuntimeError("debugfs is required to record non-root image ownership")
                    commands = temporary / "ownership.debugfs"
                    commands.write_text("".join(
                        f'set_inode_field "/{path}" uid {owner[0]}\nset_inode_field "/{path}" gid {owner[1]}\n'
                        for path, owner in sorted(owned_paths.items())))
                    changed = subprocess.run([debugfs, "-w", "-f", str(commands), str(root_image)],
                                             capture_output=True, text=True, check=False)
                    if changed.returncode or any(message in changed.stderr for message in (
                            "File not found", "Command not found", "Usage:", "while ", "Invalid")):
                        raise RuntimeError(f"Numeric image ownership failed: {changed.stderr.strip()}")
                stream.seek(root_start * SECTOR)
                with root_image.open("rb") as source:
                    shutil.copyfileobj(source, stream, length=1024 * 1024)
        validate_disk(staged)
        staged.replace(output)
        result = {"path": str(output.resolve()), "image": str(output.resolve()), "size": output.stat().st_size,
                  "sha256": sha256(output), "disk_uuid": str(identity), "esp": fat,
                  "root_uuid": str(root_id) if root else None, "root_source_identity": root_identity,
                  "payload": "efi-kernel-initramfs", "root_mode": "ext4" if root else "ram-only",
                  "root_ownership": {"default": [0, 0], "directory_overrides": policy} if root else None}
        manifest = output.with_suffix(output.suffix + ".json")
        result["manifest"] = str(manifest.resolve())
        manifest.write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        shutil.rmtree(temporary)


def validate_disk(path: Path) -> dict:
    """Validate both GPT tables and partition boundaries before VM execution."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size % SECTOR:
        raise RuntimeError("Expected a regular sector-aligned disk image")
    sectors = path.stat().st_size // SECTOR
    if sectors < 4096:
        raise RuntimeError("Disk image is too small")
    tables = []
    with path.open("rb") as stream:
        mbr = stream.read(SECTOR)
        if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xEE:
            raise RuntimeError("Disk is missing its protective MBR")
        for location in (1, sectors - 1):
            stream.seek(location * SECTOR)
            raw = stream.read(SECTOR)
            if raw[:8] != b"EFI PART":
                raise RuntimeError("Missing GPT header")
            fields = struct.unpack_from("<8sIIIIQQQQ16sQIII", raw)
            _, revision, length, checksum, reserved, current, backup, first, last, disk_id, entries_lba, count, size, crc = fields
            if (revision != 0x10000 or length != 92 or reserved or current != location
                    or backup != (sectors - 1 if location == 1 else 1)
                    or first != 34 or last != sectors - 34 or count != 128 or size != 128
                    or entries_lba != (2 if location == 1 else sectors - 33)):
                raise RuntimeError("Unexpected GPT geometry")
            header = bytearray(raw[:length])
            struct.pack_into("<I", header, 16, 0)
            if zlib.crc32(header) != checksum:
                raise RuntimeError("GPT header checksum mismatch")
            stream.seek(entries_lba * SECTOR)
            entries = stream.read(count * size)
            if len(entries) != count * size or zlib.crc32(entries) != crc:
                raise RuntimeError("GPT partition table checksum mismatch")
            tables.append((disk_id, entries))
        if tables[0] != tables[1]:
            raise RuntimeError("Primary and backup GPT tables disagree")
        partitions = []
        for index in range(128):
            entry = tables[0][1][index * 128:(index + 1) * 128]
            kind, identity, first, last, attributes, name = struct.unpack("<16s16sQQQ72s", entry)
            if kind == b"\0" * 16:
                continue
            if not 34 <= first <= last <= sectors - 34:
                raise RuntimeError("GPT partition lies outside usable disk sectors")
            if any(first <= part["last"] and last >= part["first"] for part in partitions):
                raise RuntimeError("GPT partitions overlap")
            partitions.append({"index": index + 1, "type": str(uuid.UUID(bytes_le=kind)),
                               "uuid": str(uuid.UUID(bytes_le=identity)), "first": first, "last": last,
                               "name": name.decode("utf-16-le").rstrip("\0")})
        if not partitions or partitions[0]["type"] != str(ESP_TYPE):
            raise RuntimeError("Disk must begin with an EFI system partition")
        stream.seek(partitions[0]["first"] * SECTOR)
        boot = stream.read(SECTOR)
        if boot[510:512] != b"\x55\xaa" or boot[82:90] != b"FAT32   ":
            raise RuntimeError("EFI partition is missing its FAT32 filesystem")
    return {"path": str(path.resolve()), "sectors": sectors, "partitions": partitions}
