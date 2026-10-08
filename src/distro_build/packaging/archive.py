"""Deterministic ALPM archives, following the upstream package specifications.

This is an archive exporter, not an installed-package database or resolver.
Installation, removal, upgrades, dependencies and config preservation use libalpm.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from typing import Mapping


class PackageError(ValueError):
    """Invalid metadata, staging content, or binary package."""


@dataclass(frozen=True)
class PackageArtifact:
    name: str
    version: str
    architecture: str
    path: Path
    sha256: str
    manifest: dict

    def as_dict(self) -> dict:
        return {"name": self.name, "version": self.version,
                "architecture": self.architecture, "path": str(self.path),
                "sha256": self.sha256, "manifest": self.manifest}


_NAME = re.compile(r"[A-Za-z0-9_+@][A-Za-z0-9._+@-]*\Z")
_ARCH = re.compile(r"[A-Za-z0-9_]+\Z")
_VERSION = re.compile(r"[^\s/:-]+\Z")
_REL = re.compile(r"[1-9][0-9]*(?:\.[0-9]+)?\Z")
_META = {".PKGINFO", ".BUILDINFO", ".MTREE", ".INSTALL", ".CHANGELOG"}
_SCALARS = {"pkgname", "pkgbase", "pkgver", "pkgdesc", "url", "builddate",
            "packager", "size", "arch"}
_MULTI = {"license", "replaces", "group", "conflict", "provides", "backup",
          "depend", "optdepend", "makedepend", "checkdepend", "xdata"}


def _hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _line(value: object) -> str:
    text = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise PackageError("metadata values cannot contain control characters")
    return text


def _linux_filename(value: str) -> bool:
    # systemd unit names can contain literal \xHH filename escapes. Preserve
    # those bytes exactly; never decode them into separators/control bytes.
    remainder = re.sub(r"\\x[0-9a-fA-F]{2}", "", value)
    return bool(value) and "\\" not in remainder and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _path(value: str) -> str:
    if not _linux_filename(value):
        raise PackageError(f"invalid package path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise PackageError(f"package path escapes root: {value!r}")
    normalized = str(path)
    if normalized in {".", ""}:
        raise PackageError("empty package path")
    return normalized


def _link(path: str, target: str) -> None:
    if not _linux_filename(target):
        raise PackageError(f"invalid symlink at {path}")
    # An absolute link is relative to the installed OS root, never the builder.
    parts = [] if target.startswith("/") else list(PurePosixPath(path).parent.parts)
    for part in PurePosixPath(target).parts:
        if part in {"/", "."}:
            continue
        if part == "..":
            if not parts:
                raise PackageError(f"symlink escapes installed root: {path} -> {target}")
            parts.pop()
        else:
            parts.append(part)


def _mtree_escape(value: str) -> str:
    return "".join(chr(byte) if 33 <= byte <= 126 and chr(byte) not in "\\#="
                   else f"\\{byte:03o}" for byte in value.encode("utf-8"))


def _mtree_unescape(value: str) -> str:
    output = bytearray()
    index = 0
    while index < len(value):
        if value[index] == "\\":
            octal = value[index + 1:index + 4]
            if len(octal) != 3 or not all(c in "01234567" for c in octal):
                raise PackageError("unsupported mtree escape")
            output.append(int(octal, 8))
            index += 4
        else:
            output.extend(value[index].encode("utf-8"))
            index += 1
    return output.decode("utf-8")


def _metadata(metadata: Mapping[str, object], stamp: int, size: int) -> tuple[dict, bytes]:
    name = _line(metadata.get("name", ""))
    version = _line(metadata.get("version", ""))
    revision = _line(metadata.get("revision", 1))
    arch = _line(metadata.get("arch", metadata.get("architecture", "x86_64")))
    epoch = int(metadata.get("epoch", 0))
    if not _NAME.fullmatch(name) or not _VERSION.fullmatch(version):
        raise PackageError("invalid package name or upstream version")
    if not _REL.fullmatch(revision) or not _ARCH.fullmatch(arch) or epoch < 0:
        raise PackageError("invalid package revision, architecture, or epoch")
    full_version = f"{epoch}:" if epoch else ""
    full_version += f"{version}-{revision}"
    base = _line(metadata.get("base", name))
    if not _NAME.fullmatch(base):
        raise PackageError("invalid package base")
    pkgtype = str(metadata.get("type", "pkg"))
    if pkgtype not in {"pkg", "split", "debug", "src"}:
        raise PackageError("invalid ALPM package type")
    values = {
        "pkgname": [name], "pkgbase": [base], "pkgver": [full_version],
        "pkgdesc": [" ".join(_line(metadata.get("description", "")).split())],
        "url": [_line(metadata.get("url", ""))], "builddate": [str(stamp)],
        "packager": [_line(metadata.get("packager", "Custom Distro builder"))],
        "size": [str(size)], "arch": [arch], "xdata": [f"pkgtype={pkgtype}"],
    }
    aliases = {"licenses": "license", "depends": "depend", "provides": "provides",
               "conflicts": "conflict", "replaces": "replaces", "backup": "backup",
               "groups": "group", "optional_depends": "optdepend",
               "build_depends": "makedepend", "test_depends": "checkdepend"}
    for source, dest in aliases.items():
        supplied = metadata.get(source, [])
        if isinstance(supplied, str):
            supplied = [supplied]
        values[dest] = [_line(item) for item in supplied]
    text = "".join(f"{key} = {value}\n" for key, items in values.items() for value in items)
    return values, text.encode("utf-8")


def _parse_pkginfo(content: bytes) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise PackageError("PKGINFO must be UTF-8") from exc
    for line in text.splitlines():
        line = line.lstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if " = " not in line:
            raise PackageError("invalid PKGINFO assignment")
        key, value = line.split(" = ", 1)
        if key not in _SCALARS | _MULTI:
            raise PackageError(f"unknown PKGINFO field: {key}")
        _line(value)
        if key in _SCALARS and key in values:
            raise PackageError(f"duplicate PKGINFO field: {key}")
        values.setdefault(key, []).append(value)
    if not _SCALARS <= values.keys():
        raise PackageError(f"missing PKGINFO fields: {sorted(_SCALARS - values.keys())}")
    if not _NAME.fullmatch(values["pkgname"][0]) or not _ARCH.fullmatch(values["arch"][0]):
        raise PackageError("invalid PKGINFO identity")
    if not re.fullmatch(r"(?:[0-9]+:)?[^\s/:-]+-[1-9][0-9]*(?:\.[0-9]+)?", values["pkgver"][0]):
        raise PackageError("invalid PKGINFO version")
    if not all(values[key][0].isdigit() for key in ("size", "builddate")):
        raise PackageError("invalid PKGINFO numeric field")
    if not any(x in {"pkgtype=pkg", "pkgtype=split", "pkgtype=debug", "pkgtype=src"}
               for x in values.get("xdata", [])):
        raise PackageError("PKGINFO v2 requires xdata pkgtype")
    return values


def _validate_buildinfo(content: bytes, package: dict[str, list[str]]) -> None:
    required = {"format", "pkgname", "pkgbase", "pkgver", "pkgarch", "pkgbuild_sha256sum",
                "packager", "builddate", "builddir", "startdir", "buildtool", "buildtoolver"}
    values = {}
    try:
        for line in content.decode("utf-8").splitlines():
            line = line.lstrip()
            if not line or line.startswith("#"):
                continue
            key, value = line.split(" = ", 1)
            _line(value)
            if key not in required | {"buildenv", "options", "installed"}:
                raise PackageError(f"unknown BUILDINFO field: {key}")
            if key in required and key in values:
                raise PackageError(f"duplicate BUILDINFO field: {key}")
            values[key] = value
    except (UnicodeError, ValueError) as exc:
        raise PackageError("invalid BUILDINFO assignments") from exc
    if not required <= values.keys() or values["format"] != "2":
        raise PackageError("BUILDINFO v2 is incomplete")
    for build, pkg in (("pkgname", "pkgname"), ("pkgbase", "pkgbase"),
                       ("pkgver", "pkgver"), ("pkgarch", "arch")):
        if values[build] != package[pkg][0]:
            raise PackageError(f"BUILDINFO identity differs: {build}")
    if not re.fullmatch(r"[a-f0-9]{64}", values["pkgbuild_sha256sum"]):
        raise PackageError("invalid BUILDINFO export-descriptor digest")
    if not values["builddate"].isdigit() or not all(values[key].startswith("/") for key in ("builddir", "startdir")):
        raise PackageError("invalid BUILDINFO timestamp or paths")


def _read_mtree(content: bytes) -> dict[str, dict[str, str]]:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
            decompressed = stream.read(128 * 1024**2 + 1)
        if len(decompressed) > 128 * 1024**2:
            raise PackageError("MTREE exceeds decompressed metadata limit")
        text = decompressed.decode("utf-8")
    except (OSError, EOFError, UnicodeError) as exc:
        raise PackageError("invalid gzip-compressed MTREE") from exc
    entries, defaults, current = {}, {}, PurePosixPath(".")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if fields[0] == "/set":
            defaults.update(dict(x.split("=", 1) for x in fields[1:]))
            continue
        if fields[0] == "/unset":
            for key in fields[1:]:
                defaults.pop(key, None)
            continue
        if fields[0] == "..":
            current = current.parent
            continue
        path = _mtree_unescape(fields[0])
        attrs = defaults | dict(x.split("=", 1) for x in fields[1:])
        if "/" not in path:
            path = str(current / path)
            if attrs.get("type") == "dir":
                current = PurePosixPath(path)
        if path in {".", "./"}:
            continue
        path = _path(path)
        if path in entries:
            raise PackageError(f"duplicate MTREE path: {path}")
        entries[path] = attrs
    return entries


def inspect_package(path: Path, *, max_bytes: int = 4 * 1024**3,
                    max_members: int = 500_000) -> dict:
    """Read without extracting; reject traversal, unsafe types and manifest drift.

    Absolute symlink targets address the installed OS root. An archive must not
    contain child entries below any symlink, even if the symlink appears last.
    Detached signatures are validated by the native toolkit, not this parser.
    """
    path = Path(path)
    entries, metadata, total = {}, {}, 0
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                name = _path(member.name)
                if name in entries:
                    raise PackageError(f"duplicate archive path: {name}")
                if len(entries) >= max_members or member.size < 0:
                    raise PackageError("package exceeds member limit")
                total += member.size
                if total > max_bytes:
                    raise PackageError("package exceeds unpacked size limit")
                if not (member.isfile() or member.isdir() or member.issym()):
                    raise PackageError(f"unsupported archive type: {name}")
                if any(key.startswith(("SCHILY.xattr.", "LIBARCHIVE.xattr."))
                       for key in member.pax_headers):
                    raise PackageError("extended attributes require a qualified exporter")
                record = {"path": name, "type": "dir" if member.isdir() else
                          "link" if member.issym() else "file", "mode": member.mode,
                          "uid": member.uid, "gid": member.gid, "size": member.size,
                          "mtime": member.mtime}
                if member.issym():
                    _link(name, member.linkname)
                    record["link"] = member.linkname
                elif member.isfile():
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise PackageError(f"unreadable archive member: {name}")
                    if name in _META:
                        if member.size > 16 * 1024**2:
                            raise PackageError("package metadata is too large")
                        content = stream.read()
                        metadata[name] = content
                        record["sha256"] = hashlib.sha256(content).hexdigest()
                    else:
                        record["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                entries[name] = record
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise PackageError(f"cannot read package {path}: {exc}") from exc
    for name in entries:
        for ancestor in PurePosixPath(name).parents:
            if str(ancestor) in entries and entries[str(ancestor)]["type"] != "dir":
                raise PackageError(f"archive path traverses a non-directory: {name}")
        if name.startswith(".") and name not in _META:
            raise PackageError(f"unrecognized reserved root metadata: {name}")
    if not {".PKGINFO", ".BUILDINFO", ".MTREE"} <= metadata.keys():
        raise PackageError("package lacks mandatory ALPM metadata")
    info = _parse_pkginfo(metadata[".PKGINFO"])
    _validate_buildinfo(metadata[".BUILDINFO"], info)
    try:
        mtree = _read_mtree(metadata[".MTREE"])
    except (ValueError, UnicodeError) as exc:
        raise PackageError("malformed MTREE attributes") from exc
    expected = set(entries) - {".MTREE"}
    if set(mtree) != expected:
        raise PackageError("MTREE paths differ from archive contents")
    for name in expected:
        entry, attrs = entries[name], mtree[name]
        for key in ("type", "uid", "gid"):
            if str(entry[key]) != attrs.get(key):
                raise PackageError(f"MTREE {key} differs for {name}")
        if int(attrs.get("mode", "-1"), 8) != entry["mode"]:
            raise PackageError(f"MTREE mode differs for {name}")
        if float(attrs.get("time", "-1")) != entry["mtime"]:
            raise PackageError(f"MTREE timestamp differs for {name}")
        if entry["type"] == "file" and (
            attrs.get("sha256digest") != entry["sha256"] or
            attrs.get("size") != str(entry["size"])
        ):
            raise PackageError(f"MTREE payload differs for {name}")
        if entry["type"] == "link" and _mtree_unescape(attrs.get("link", "")) != entry["link"]:
            raise PackageError(f"MTREE link differs for {name}")
    for backup in info.get("backup", []):
        normalized = _path(backup)
        if backup != normalized or entries.get(backup, {}).get("type") != "file":
            raise PackageError(f"backup is not a packaged regular file: {backup}")
    return {"name": info["pkgname"][0], "version": info["pkgver"][0],
            "architecture": info["arch"][0], "metadata": info,
            "files": [entries[key] for key in sorted(entries) if key not in _META],
            "sha256": _hash_file(path)}


def export_package(stage: Path, output_dir: Path, metadata: Mapping[str, object], *,
                   source_date_epoch: int, provenance: Mapping | None = None,
                   ownership: Mapping[str, tuple[int, int]] | None = None,
                   install_script: Path | None = None) -> PackageArtifact:
    """Export exactly the staged files, without stripping or generated outputs.

    Default ownership is root:root, independent of the build user's UID.
    Device nodes, FIFOs, sockets and xattrs fail explicitly in this first backend.
    Hardlinked staging files are represented as independent regular files.
    """
    stage, output_dir = Path(stage).resolve(), Path(output_dir).resolve()
    stamp = int(source_date_epoch)
    if stamp < 0 or not stage.is_dir():
        raise PackageError("invalid source timestamp or staging directory")
    ownership = ownership or {}
    records, sources = {}, {}
    for current, dirs, files in os.walk(stage, followlinks=False):
        for leaf in sorted(dirs + files):
            source = Path(current) / leaf
            name = _path(source.relative_to(stage).as_posix())
            if name.startswith("."):
                raise PackageError(f"reserved root metadata in stage: {name}")
            status = source.lstat()
            if not (stat.S_ISDIR(status.st_mode) or stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode)):
                raise PackageError(f"unsupported staged file type: {name}")
            if hasattr(os, "listxattr") and os.listxattr(source, follow_symlinks=False):
                raise PackageError(f"extended attributes cannot be silently dropped: {name}")
            uid, gid = ownership.get(name, (0, 0))
            if not isinstance(uid, int) or not isinstance(gid, int) or min(uid, gid) < 0:
                raise PackageError(f"invalid numeric ownership: {name}")
            kind = "link" if source.is_symlink() else "dir" if source.is_dir() else "file"
            record = {"path": name, "type": kind, "mode": stat.S_IMODE(status.st_mode),
                      "uid": uid, "gid": gid, "mtime": stamp, "size": status.st_size if kind == "file" else 0}
            if kind == "link":
                record["link"] = os.readlink(source)
                _link(name, record["link"])
            if kind == "file":
                record["sha256"] = _hash_file(source)
            records[name], sources[name] = record, source
    if set(ownership) - records.keys():
        raise PackageError("ownership manifest references missing staged paths")
    info, pkginfo = _metadata(metadata, stamp, sum(item["size"] for item in records.values()))
    for backup in info.get("backup", []):
        if _path(backup) != backup or records.get(backup, {}).get("type") != "file":
            raise PackageError(f"backup is not a staged regular file: {backup}")
    # An actual, deterministic export descriptor supplies BUILDINFO's PKGBUILD digest.
    descriptor = (f"# Export descriptor; compilation is recorded in the build manifest.\n"
                  f"pkgname={info['pkgname'][0]!r}\npkgver={str(metadata['version'])!r}\n"
                  f"pkgrel={str(metadata.get('revision', 1))!r}\narch=({info['arch'][0]!r})\n"
                  "package() { cp -a -- \"$srcdir/stage/.\" \"$pkgdir/\"; }\n").encode()
    descriptor_hash = hashlib.sha256(descriptor).hexdigest()
    buildinfo = {
        "format": 2, "pkgname": info["pkgname"][0], "pkgbase": info["pkgbase"][0],
        "pkgver": info["pkgver"][0], "pkgarch": info["arch"][0],
        "pkgbuild_sha256sum": descriptor_hash, "packager": info["packager"][0],
        "builddate": stamp, "builddir": "/build", "startdir": "/build/export",
        "buildtool": "custom-distro-build", "buildtoolver": "0.1.0",
    }
    buildtext = "".join(f"{key} = {value}\n" for key, value in buildinfo.items())
    for value in (provenance or {}).get("installed", []):
        buildtext += f"installed = {_line(value)}\n"
    blobs = {".PKGINFO": pkginfo, ".BUILDINFO": buildtext.encode()}
    if install_script:
        blobs[".INSTALL"] = Path(install_script).read_bytes()
    for name, content in blobs.items():
        records[name] = {"path": name, "type": "file", "mode": 0o644, "uid": 0,
                         "gid": 0, "mtime": stamp, "size": len(content),
                         "sha256": hashlib.sha256(content).hexdigest()}
    mtree = ["#mtree\n"]
    for name in sorted(records):
        entry = records[name]
        line = (f"./{_mtree_escape(name)} type={entry['type']} uid={entry['uid']} "
                f"gid={entry['gid']} mode={entry['mode']:o} time={stamp}.0")
        if entry["type"] == "file":
            line += f" size={entry['size']} sha256digest={entry['sha256']}"
        elif entry["type"] == "link":
            line += f" link={_mtree_escape(entry['link'])}"
        mtree.append(line + "\n")
    blobs[".MTREE"] = gzip.compress("".join(mtree).encode(), mtime=0)
    records[".MTREE"] = {"path": ".MTREE", "type": "file", "mode": 0o644,
                         "uid": 0, "gid": 0, "mtime": stamp, "size": len(blobs[".MTREE"])}
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{info['pkgname'][0]}-{info['pkgver'][0]}-{info['arch'][0]}.pkg.tar.xz"
    destination = output_dir / filename
    with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".pkg.tar.xz", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with tarfile.open(temporary, "w:xz", format=tarfile.PAX_FORMAT, preset=6) as archive:
            # Metadata first, so native readers do not scan the payload for identity.
            for name in sorted(records, key=lambda x: (x not in _META, x)):
                entry = records[name]
                item = tarfile.TarInfo(name + ("/" if entry["type"] == "dir" else ""))
                item.mode, item.uid, item.gid, item.mtime = entry["mode"], entry["uid"], entry["gid"], stamp
                item.uname = item.gname = ""
                if entry["type"] == "dir":
                    item.type = tarfile.DIRTYPE
                    archive.addfile(item)
                elif entry["type"] == "link":
                    item.type, item.linkname = tarfile.SYMTYPE, entry["link"]
                    archive.addfile(item)
                else:
                    item.size = entry["size"]
                    if name in blobs:
                        archive.addfile(item, io.BytesIO(blobs[name]))
                    else:
                        with sources[name].open("rb") as content:
                            archive.addfile(item, content)
        inspected = inspect_package(temporary)
        if destination.exists():
            if _hash_file(destination) != inspected["sha256"]:
                raise PackageError(f"refusing to overwrite published package identity: {filename}")
            temporary.unlink()
        else:
            os.replace(temporary, destination)
        manifest = {"schema_version": 1, "name": inspected["name"], "version": inspected["version"],
                    "architecture": inspected["architecture"], "sha256": inspected["sha256"],
                    "files": inspected["files"], "build": dict(provenance or {}),
                    "export_descriptor_sha256": descriptor_hash}
        destination.with_name(destination.name + ".build.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        destination.with_name(destination.name + ".PKGBUILD").write_bytes(descriptor)
        return PackageArtifact(inspected["name"], inspected["version"], inspected["architecture"],
                               destination, inspected["sha256"], manifest)
    finally:
        temporary.unlink(missing_ok=True)
