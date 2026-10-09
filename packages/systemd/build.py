#!/usr/bin/env python3
"""Build the source-built systemd target package with its declared sysroot."""
from pathlib import Path
import json
import os
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from distro_build.system_runtime import build_system_runtime


def normalize_journal_directory(stage: Path, work: Path) -> None:
    # Upstream's install script runs the host setfacl even with -Dacl=disabled.
    # Those host adm/wheel group ACLs are not target policy. Preserve the empty
    # journal directory; the installed tmpfiles rule assigns its guest group.
    journal = stage / "var/log/journal"
    if journal.is_symlink() or not journal.is_dir() or any(journal.iterdir()):
        raise RuntimeError("Expected an empty staged systemd journal directory")
    removed = []
    for name in os.listxattr(journal, follow_symlinks=False):
        if name not in {"system.posix_acl_access", "system.posix_acl_default"}:
            raise RuntimeError(f"Unexpected journal directory attribute: {name}")
        os.removexattr(journal, name, follow_symlinks=False)
        removed.append(name)
    (work / "journal-directory-policy.json").write_text(json.dumps({
        "path": "var/log/journal", "removed_native_install_attributes": sorted(removed),
        "target_acl_support": False,
        "guest_ownership": "usr/lib/tmpfiles.d/systemd.conf: root:systemd-journal, mode 2755",
    }, indent=2) + "\n")


if __name__ == "__main__":
    build_system_runtime('systemd')
    normalize_journal_directory(Path(os.environ["CD_STAGE_DIR"]), Path(os.environ["CD_WORK_DIR"]))
