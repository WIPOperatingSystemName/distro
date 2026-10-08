"""Private native build stack for early libalpm image-assembly experiments.

This explicitly uses declared host compiler/libc/zlib seed inputs. These native
tools are not copied into the target OS. Public updates need a separately
qualified toolkit with source-built curl/GPGME and configured signing trust.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tarfile
import tomllib
from urllib.request import urlopen

from .toolkit import PacmanToolkit, ToolkitError, _run, build_toolkit


def build_development_toolkit(output: Path, *, cache: Path | None = None,
                              jobs: int = 2, fetch: bool = False) -> PacmanToolkit:
    """Verify pins, compile libraries/pacman, and install only below output.

    Network downloads require explicit fetch=True. Prepared archives are
    accepted from cache using upstream filenames and verified before extraction.
    """
    output = Path(output).resolve()
    cache = Path(cache or output / "sources").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    pins = tomllib.loads(Path(__file__).with_name("sources.toml").read_text())
    prefix, work = output / "prefix", output / "work"
    prefix.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    environment = {
        "PATH": str(prefix / "bin") + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C", "PKG_CONFIG_PATH": str(prefix / "lib/pkgconfig"),
        "LD_LIBRARY_PATH": str(prefix / "lib"),
        "CPPFLAGS": "-I" + str(prefix / "include"),
        "LDFLAGS": "-L" + str(prefix / "lib") + " -Wl,-rpath," + str(prefix / "lib"),
        "SOURCE_DATE_EPOCH": "1761955200",
    }
    trees = {}
    for name, pin in pins.items():
        archive = cache / pin["url"].rsplit("/", 1)[1]
        if not archive.exists():
            if not fetch:
                raise ToolkitError(f"missing source archive {archive}; prepare it or enable fetch")
            with urlopen(pin["url"], timeout=60) as response, archive.with_suffix(archive.suffix + ".part").open("wb") as destination:
                while block := response.read(1024 * 1024):
                    destination.write(block)
            archive.with_suffix(archive.suffix + ".part").replace(archive)
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != pin["sha256"]:
            raise ToolkitError(f"source checksum mismatch: {archive.name}")
        tree = work / pin["directory"]
        if not tree.exists():
            with tarfile.open(archive) as source:
                source.extractall(work, filter="data")
        trees[name] = tree
    log = output / "dependencies-build.log"
    marker = prefix / ".development-dependencies.json"
    implementation = b"".join(Path(__file__).with_name(name).read_bytes()
                              for name in ("bootstrap_toolkit.py", "toolkit.py", "native_install.c"))
    identity = hashlib.sha256(json.dumps(pins, sort_keys=True).encode() + implementation).hexdigest()
    # Changes to transaction helpers rebuild pacman/helper, not unchanged native
    # dependency libraries. Library recipe changes still require a fresh prefix.
    dependency_identity = hashlib.sha256(json.dumps(pins, sort_keys=True).encode()
                                         + Path(__file__).read_bytes()).hexdigest()
    prior = json.loads(marker.read_text()) if marker.exists() else None
    if prior and prior.get("pins") != pins:
        raise ToolkitError("native dependency source pins changed; rebuild in a fresh output directory")
    if prior and prior.get("dependency_identity") not in {None, dependency_identity}:
        raise ToolkitError("native dependency library recipes changed; rebuild in a fresh output directory")
    if prior and prior.get("dependency_identity") is None:
        # One inspected migration from the original combined identity. Its
        # library build commands and pins are unchanged; preserve the original
        # compilation receipt rather than claiming libraries were rebuilt.
        legacy_identity = "270f14c5bd9642f1f4a6f9799004536c7a26c827851daab3de7c852a321b6a2a"
        if prior.get("identity") != legacy_identity:
            raise ToolkitError("unrecognized legacy native dependency receipt; rebuild in a fresh output directory")
        prior["dependency_identity"] = dependency_identity
        prior["provenance_migration"] = {
            "original_stack_identity": legacy_identity,
            "original_library_recipe_sha256": "520bc5f0dbc96689c73003e70c4985d5d774c68e00b03395e006a15336984958",
            "reason": "separate unchanged library compilation from private transaction helper changes",
            "reused_library_hashes": {
                str(path.relative_to(prefix)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((prefix / "lib").iterdir())
                if path.is_file() and not path.is_symlink()
                and path.name.startswith(("libarchive.", "liblzma.", "libcrypto.", "libssl."))},
        }
        marker.write_text(json.dumps(prior, sort_keys=True, indent=2) + "\n")
    if not marker.exists():
        _run([str(trees["xz"] / "configure"), "--prefix=" + str(prefix),
              "--libdir=" + str(prefix / "lib"), "--disable-nls", "--disable-doc"],
             env=environment, cwd=trees["xz"], log=log)
        _run(["make", "-j" + str(jobs)], env=environment, cwd=trees["xz"], log=log)
        _run(["make", "install"], env=environment, cwd=trees["xz"], log=log)
        _run(["perl", "./Configure", "linux-x86_64", "--prefix=" + str(prefix),
              "--libdir=lib", "--openssldir=" + str(prefix / "etc/ssl"), "shared", "no-tests"],
             env=environment, cwd=trees["openssl"], log=log)
        _run(["make", "-j" + str(jobs)], env=environment, cwd=trees["openssl"], log=log)
        _run(["make", "install_sw"], env=environment, cwd=trees["openssl"], log=log)
        _run([str(trees["libarchive"] / "configure"), "--prefix=" + str(prefix),
              "--libdir=" + str(prefix / "lib"), "--without-zstd", "--without-bz2lib",
              "--without-lz4", "--without-xml2", "--without-expat", "--without-iconv",
              "--without-libb2", "--disable-acl", "--disable-xattr"],
             env=environment, cwd=trees["libarchive"], log=log)
        _run(["make", "-j" + str(jobs)], env=environment, cwd=trees["libarchive"], log=log)
        _run(["make", "install"], env=environment, cwd=trees["libarchive"], log=log)
        zlib = Path(_run(["cc", "-print-file-name=libz.so"], env=environment).strip()).resolve()
        marker.write_text(json.dumps({"identity": identity, "dependency_identity": dependency_identity,
                                     "pins": pins, "seed": {
            "compiler": _run(["cc", "--version"], env=environment).splitlines()[0],
            "host_libc": _run(["getconf", "GNU_LIBC_VERSION"], env=environment).strip(),
            "zlib": {"scope": "native tools only", "path": str(zlib),
                     "sha256": hashlib.sha256(zlib.read_bytes()).hexdigest(),
                     "header_sha256": hashlib.sha256(Path("/usr/include/zlib.h").read_bytes()).hexdigest()},
        }}, indent=2, sort_keys=True) + "\n")
    toolkit_record = prefix / "toolkit.json"
    if toolkit_record.exists():
        record = json.loads(toolkit_record.read_text())
        if record.get("source_pins") == pins and record.get("stack_identity") == identity:
            return PacmanToolkit(prefix)
    toolkit = build_toolkit(trees["pacman"], prefix, work, environment, development=True, jobs=jobs)
    dependencies_receipt = json.loads(marker.read_text())
    record = toolkit.record | {"source_pins": pins, "stack_identity": identity,
                              "dependency_build": dependencies_receipt,
                              "seed_inputs": dependencies_receipt["seed"]}
    toolkit_record.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    return PacmanToolkit(prefix)
