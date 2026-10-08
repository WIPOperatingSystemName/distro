"""Keep installed SDK flags portable while preserving exact compile receipts."""
from pathlib import Path
import hashlib
import re


def normalize_sdk_metadata(stage: Path, sysroot: Path, package: str) -> list[dict]:
    if package not in {"curl", "libarchive"}:
        raise ValueError("unsupported SDK metadata normalization")
    module = "libcurl" if package == "curl" else "libarchive"
    paths = [stage / f"usr/lib/pkgconfig/{module}.pc"]
    if package == "curl":
        paths.append(stage / "usr/bin/curl-config")
    records = []
    for path in paths:
        original = path.read_text()
        updated = original.replace("-L" + str(sysroot / "usr/lib"),
                                   "-L${libdir}" if path.suffix == ".pc" else "")
        if path.name == "curl-config":
            updated = re.sub(r"(--cc\)\n)\s+echo [^\n]+", r'\1    echo "${CC:-cc}"', updated)
            updated = re.sub(r"\s+'(?:--with-sysroot=|CC=)[^']*'", "", updated)
            updated = updated.replace("the arguments given to configure when building curl",
                                      "portable configure options (exact build recorded in manifest)")
        for private in (str(sysroot), str(sysroot.parent)):
            if private in updated:
                raise RuntimeError(f"installed SDK metadata still contains a private build path: {path}")
        path.write_text(updated)
        records.append({"path": str(path.relative_to(stage)),
                        "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
                        "sha256": hashlib.sha256(updated.encode()).hexdigest()})
    return records
