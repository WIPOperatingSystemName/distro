#!/usr/bin/env python3
from pathlib import Path
import hashlib
import json
import os
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from distro_build.desktop_services import build_desktop_service


def configure_bwrap(source: Path, sysroot: Path, work: Path) -> None:
    # Meson searches the build host for programs. Detect our declared target
    # dependency in the sysroot, but compile its guest installation path into
    # the validators. The extracted archive is a private recipe build input.
    target = sysroot / "usr/bin/bwrap"
    if not target.is_file() or not os.access(target, os.X_OK):
        raise RuntimeError("The declared Bubblewrap target dependency is missing")
    changes = []
    for relative, before, after, count in [
        ("meson.build", "bwrap = find_program('bwrap',", f"bwrap = find_program({str(target)!r},", 1),
        ("src/meson.build", "bwrap.full_path()", "'/usr/bin/bwrap'", 2),
    ]:
        path = source / relative
        original = path.read_text()
        if original.count(before) != count:
            raise RuntimeError(f"Portal Bubblewrap cross-build anchor changed: {relative}")
        updated = original.replace(before, after)
        path.write_text(updated)
        changes.append({"path": relative, "before_sha256": hashlib.sha256(original.encode()).hexdigest(),
                        "after_sha256": hashlib.sha256(updated.encode()).hexdigest()})
    (work / "bubblewrap-cross-build.json").write_text(json.dumps({
        "target_dependency": str(target), "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "guest_program": "/usr/bin/bwrap", "source_changes": changes,
    }, indent=2) + "\n")


if __name__ == "__main__":
    configure_bwrap(Path(os.environ["CD_SOURCE_DIR"]), Path(os.environ["CD_SYSROOT"]),
                    Path(os.environ["CD_WORK_DIR"]))
    build_desktop_service('xdg-desktop-portal')
