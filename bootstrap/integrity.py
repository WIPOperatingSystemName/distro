"""Content receipts for temporary bootstrap artifacts and declared host seeds."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess


def fingerprint(value: object) -> str:
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(paths: list[Path]) -> list[dict]:
    entries: dict[str, dict] = {}
    for root in paths:
        if not root.exists() and not root.is_symlink():
            raise ValueError(f"Missing bootstrap artifact: {root}")
        descendants = list(root.rglob("*")) if root.is_dir() and not root.is_symlink() else []
        for path in [root, *descendants]:
            attributes = path.lstat()
            row = {"path": str(path), "mode": stat.S_IMODE(attributes.st_mode)}
            if path.is_symlink():
                row |= {"kind": "symlink", "target": os.readlink(path)}
            elif path.is_file():
                row |= {"kind": "file", "sha256": sha256(path)}
            elif path.is_dir():
                row |= {"kind": "directory"}
            else:
                raise ValueError(f"Unsupported bootstrap artifact type: {path}")
            entries[str(path)] = row
    return [entries[key] for key in sorted(entries)]


def seed_identity() -> dict:
    inputs = {}
    for name in ["gcc", "g++", "ld", "as", "make", "bison", "m4", "perl", "python3",
                 "tar", "xz", "bzip2", "gzip", "sed", "grep", "find", "awk", "bc", "pkg-config",
                 "bash", "install", "cp", "ln", "ar", "nm", "ranlib", "strip",
                 "objcopy", "objdump", "readelf", "cat", "chmod", "cmp", "cut", "date",
                 "diff", "dirname", "env", "expr", "head", "ls", "mkdir", "mv", "readlink",
                 "rm", "rmdir", "sort", "tail", "touch", "tr", "uname", "wc"]:
        executable = Path("/usr/bin") / name
        if not executable.is_file():
            raise ValueError(f"Missing declared native seed: {executable}")
        command = [str(executable), "-W", "version"] if name == "awk" else [str(executable), "--version"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20,
                                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"})
        lines = (result.stdout + result.stderr).strip().splitlines()
        inputs[name] = {"path": str(executable.resolve()), "sha256": sha256(executable),
                        "version": lines[0] if lines else "unavailable"}
    # elfutils' native generator uses the host development zlib, never target libz.
    zlib_headers = [Path("/usr/include/zlib.h"), Path("/usr/include/zconf.h")]
    zlib_library = Path("/usr/lib/x86_64-linux-gnu/libz.so")
    zlib_inputs = zlib_headers + ([zlib_library, zlib_library.resolve()] if zlib_library.exists() else [])
    compiler_inputs = []
    for command in ["gcc", "g++"]:
        for argument in ["-print-prog-name=cc1", "-print-prog-name=cc1plus",
                         "-print-prog-name=collect2", "-print-prog-name=lto1",
                         "-print-file-name=libgcc.a", "-print-file-name=libstdc++.so",
                         "-print-file-name=libgcc_s.so", "-print-file-name=crtbegin.o"]:
            result = subprocess.check_output([f"/usr/bin/{command}", argument], text=True).strip()
            path = Path(result)
            if path.is_file():
                compiler_inputs.extend([path, path.resolve()])
    libc_inputs = [Path(path) for path in ["/usr/include/features.h", "/usr/include/stdio.h",
                  "/usr/lib/x86_64-linux-gnu/libc.so", "/usr/lib/x86_64-linux-gnu/libc.so.6",
                  "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
                  "/usr/lib/x86_64-linux-gnu/crt1.o", "/usr/lib/x86_64-linux-gnu/crti.o",
                  "/usr/lib/x86_64-linux-gnu/crtn.o"] if Path(path).exists()]
    return {"machine": os.uname().machine, "native_seeds": inputs,
            "native_zlib_seed": receipt(zlib_inputs),
            "native_compiler_support": receipt(list(dict.fromkeys(compiler_inputs))),
            "native_libc_seed": receipt(libc_inputs),
            "scope": "declared command/compiler/native generator seeds; not a complete host image identity"}


def stage_artifacts(name: str, mandatory: list[Path], root: Path, tools: Path, target: str) -> list[Path]:
    paths = list(mandatory)
    extra = []
    if name == "binutils":
        extra = [tools / "bin" / f"{target}-{command}" for command in
                 ["addr2line", "ar", "as", "c++filt", "elfedit", "gprof", "ld", "nm",
                  "objcopy", "objdump", "ranlib", "readelf", "size", "strings", "strip"]]
        extra.extend([tools / target / "bin", tools / target / "lib/ldscripts"])
    elif name == "gcc":
        extra = [tools / "lib/gcc" / target / "15.2.0", tools / "libexec/gcc" / target / "15.2.0"]
        extra.extend((tools / "bin").glob(f"{target}-gc*"))
        extra.append(tools / "bin" / f"{target}-cpp")
    elif name == "gawk-native":
        extra = [tools / "native/bin/awk", tools / "native/lib/gawk"]
    elif name == "flex-native":
        extra = list((tools / "native/lib").glob("libfl*"))
    elif name == "elfutils-native":
        extra = list((tools / "native/lib").glob("libelf*"))
        extra += [tools / "native/include/nlist.h", tools / "native/include/elfutils/elf-knowledge.h"]
    elif name == "headers":
        # glibc installs additional headers later; keep the kernel API receipt
        # limited to the directories that headers_install actually owns.
        extra = [root / "usr/include" / part for part in ["asm", "asm-generic", "drm",
                 "linux", "misc", "mtd", "rdma", "regulator", "scsi", "sound", "video", "xen"]]
    elif name == "glibc":
        extra = [root / "usr/lib", root / "usr/include", root / "lib", root / "lib64"]
    elif name == "gcc-pass2":
        extra = [tools / "pass2"]
    paths.extend(path for path in extra if path.exists() or path.is_symlink())
    return list(dict.fromkeys(paths))
