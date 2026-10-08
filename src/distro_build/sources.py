"""Verified source caching and traversal-safe archive extraction."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

from .model import BuildError, Source


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(source: Source, cache: Path, *, offline: bool = False) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / source.filename
    if destination.exists():
        if sha256(destination) != source.sha256:
            raise BuildError(f"{source.name}: cached source checksum mismatch: {destination}")
        return destination
    if offline:
        raise BuildError(f"{source.name}: source absent from offline cache")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix=f".{source.filename}.", delete=False) as target:
            temporary = Path(target.name)
            request = urllib.request.Request(source.url, headers={"User-Agent": "Custom-Distro-Build/0.1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.geturl().startswith("https://"):
                    raise BuildError(f"{source.name}: rejected non-HTTPS redirect")
                shutil.copyfileobj(response, target, length=1024 * 1024)
        actual = sha256(temporary)
        if actual != source.sha256:
            raise BuildError(f"{source.name}: SHA256 mismatch; expected {source.sha256}, got {actual}")
        os.replace(temporary, destination)
        return destination
    except (OSError, urllib.error.URLError) as error:
        raise BuildError(f"{source.name}: fetch failed: {error}") from error
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def fetch_all(sources: list[Source], cache: Path, *, offline: bool = False, workers: int = 4) -> list[Path]:
    distinct = {}
    for source in sources:
        prior = distinct.get(source.filename)
        if prior and prior.sha256 != source.sha256:
            raise BuildError(f"conflicting source pins for filename {source.filename}")
        distinct[source.filename] = source
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda source: fetch(source, cache, offline=offline), distinct.values()))


def extract(archive: Path, destination: Path, *, max_bytes: int = 8 * 1024**3) -> Path:
    if destination.exists():
        raise BuildError(f"source extraction destination already exists: {destination}")
    destination.mkdir(parents=True)
    try:
        with tarfile.open(archive) as source:
            members = source.getmembers()
            if sum(max(member.size, 0) for member in members) > max_bytes:
                raise BuildError(f"source archive exceeds extraction size limit: {archive}")
            source.extractall(destination, members=members, filter="data")
        entries = list(destination.iterdir())
        return entries[0] if len(entries) == 1 and entries[0].is_dir() else destination
    except (tarfile.TarError, OSError) as error:
        shutil.rmtree(destination)
        raise BuildError(f"unsafe or invalid source archive {archive}: {error}") from error
