"""Validated catalog records, independent of build execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


class BuildError(RuntimeError):
    pass


_NAME = re.compile(r"[a-z0-9][a-z0-9+_.-]*\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    sha256: str
    filename: str
    format: str = "archive"

    @classmethod
    def parse(cls, value: dict, where: str) -> Source:
        if not isinstance(value, dict):
            raise BuildError(f"{where}: source must be a table")
        name = value.get("name", value.get("id"))
        url = value.get("url")
        digest = value.get("sha256")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise BuildError(f"{where}: invalid source name")
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            raise BuildError(f"{where}: source URL must use HTTPS")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise BuildError(f"{where}: source requires a lowercase SHA256 pin")
        filename = value.get("filename", Path(urlsplit(url).path).name)
        if not isinstance(filename, str) or filename in ("", ".", "..") or Path(filename).name != filename:
            raise BuildError(f"{where}: invalid source filename")
        source_format = value.get("format", "archive")
        if source_format not in {"archive", "file"}:
            raise BuildError(f"{where}: unsupported source format")
        return cls(name, url, digest, filename, source_format)


@dataclass(frozen=True)
class Recipe:
    path: Path
    package: dict
    sources: tuple[Source, ...]
    build: dict
    dependencies: dict[str, tuple[str, ...]]

    @property
    def name(self) -> str:
        return self.package["name"]

    @classmethod
    def load(cls, path: Path) -> Recipe:
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise BuildError(f"{path}: {error}") from error
        if data.get("schema") != 1:
            raise BuildError(f"{path}: unsupported recipe schema")
        unknown = set(data) - {"schema", "package", "sources", "dependencies", "build"}
        if unknown:
            raise BuildError(f"{path}: unknown recipe fields: {sorted(unknown)}")
        package = data.get("package", {})
        for key in ("name", "version", "arch", "description", "license"):
            if not isinstance(package.get(key), str) or not package[key]:
                raise BuildError(f"{path}: package.{key} must be a nonempty string")
        if not _NAME.fullmatch(package["name"]):
            raise BuildError(f"{path}: invalid package name")
        if not isinstance(package.get("revision"), int) or package["revision"] < 1:
            raise BuildError(f"{path}: package.revision must be a positive integer")
        sources = tuple(Source.parse(s, str(path)) for s in data.get("sources", []))
        if not sources or len({s.name for s in sources}) != len(sources):
            raise BuildError(f"{path}: sources must have distinct names")
        build = data.get("build", {})
        if build.get("adapter") not in {"script", "autotools", "make"}:
            raise BuildError(f"{path}: unsupported build.adapter")
        if build["adapter"] == "script":
            commands = build.get("commands")
            if not isinstance(commands, list) or not commands or any(
                not isinstance(c, list) or not c or any(not isinstance(a, str) for a in c)
                for c in commands
            ):
                raise BuildError(f"{path}: build.commands must be command argument arrays")
        dependencies = {}
        for role, names in data.get("dependencies", {}).items():
            if role not in {"native", "target", "runtime", "test"}:
                raise BuildError(f"{path}: unknown dependency role {role}")
            if not isinstance(names, list) or any(not isinstance(n, str) or not _NAME.fullmatch(n) for n in names):
                raise BuildError(f"{path}: invalid {role} dependencies")
            if role == "native" and names:
                raise BuildError(f"{path}: named native package namespace is not implemented; "
                                 "declare an explicit host seed or source-pinned private native helper")
            dependencies[role] = tuple(names)
        return cls(path.resolve(), package, sources, build, dependencies)


def catalog(project: Path) -> dict[str, Recipe]:
    result = {}
    for path in sorted((project / "packages").glob("*/package.toml")):
        recipe = Recipe.load(path)
        if recipe.name in result:
            raise BuildError(f"{path}: duplicate package {recipe.name}")
        result[recipe.name] = recipe
    return result
