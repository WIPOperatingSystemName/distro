"""Persistent Cargo inputs; immutable source snapshots remain the build record."""
from __future__ import annotations

import filecmp
from pathlib import Path
import shutil


def synchronize(source: Path, destination: Path) -> None:
    """Preserve unchanged mtimes, remove deleted inputs, and touch changed files.

    Cargo tracks paths and mtimes. Building from a new content-addressed path
    on every edit defeats reuse even when CARGO_TARGET_DIR stays the same.
    Callers must hold the application's build lock throughout synchronization
    and compilation. Snapshots contain regular files/directories only.
    """
    if destination.is_symlink():
        raise RuntimeError("Cargo workspace cannot be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    for child in destination.iterdir():
        if not (source / child.name).exists():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    for child in source.iterdir():
        target = destination / child.name
        if child.is_symlink():
            raise RuntimeError("Cargo snapshots must not contain symlinks")
        if target.is_symlink() or (target.exists() and target.is_dir() != child.is_dir()):
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if child.is_dir():
            synchronize(child, target)
        elif not target.is_file() or not filecmp.cmp(child, target, shallow=False):
            # copyfile deliberately gives edits a current mtime, including when
            # an older source revision has been restored.
            shutil.copyfile(child, target)
            shutil.copymode(child, target)
        else:
            shutil.copymode(child, target)
