#!/usr/bin/env python3
"""Re-export verified curl/libarchive metadata and qualify genuine SDK consumers.

Compiled payloads are retained byte for byte with their original provenance.
Canonical state changes only after a source-built static libarchive consumer and
libcurl pkg-config --static dependency flags pass the private target linker audit.
"""
from pathlib import Path
import fcntl
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tarfile

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))
from distro_build.desktop_native import _link_wrapper
from distro_build.graph import plan
from distro_build.model import catalog
from distro_build.packaging import PacmanToolkit, export_package, inspect_package
from distro_build.packaging.sdk_metadata import normalize_sdk_metadata
from distro_build.runner import check_bootstrap

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def command(arguments, env):
    result = subprocess.run(list(map(str, arguments)), env=env, text=True, capture_output=True)
    result.check_returncode()
    return result.stdout

def prepare(name, recipe):
    state = project / "out/state/packages" / f"{name}.json"
    previous = json.loads(state.read_text())
    original = previous["provenance"]
    info = inspect_package(Path(previous["path"]))
    if info["sha256"] != previous["sha256"]:
        raise RuntimeError("Existing source compilation archive differs from its receipt")
    if info["version"] == f"{recipe.package['version']}-{recipe.package['revision']}":
        return previous
    if info["version"] != f"{recipe.package['version']}-{recipe.package['revision'] - 1}":
        raise RuntimeError("Metadata correction requires the immediately preceding package revision")
    inputs = {"compiled_identity": original.get("compilation_build_identity", original["build_identity"]),
              "original_package_sha256": previous["sha256"], "recipe_sha256": digest(recipe.path),
              "normalizer_sha256": digest(project / "src/distro_build/packaging/sdk_metadata.py"),
              "exporter_sha256": digest(__file__)}
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    work = project / "out/work" / name / identity
    work.mkdir(parents=True, exist_ok=True)
    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    with tarfile.open(previous["path"]) as archive:
        archive.extractall(stage, filter="data")
    for name in (".PKGINFO", ".BUILDINFO", ".MTREE", ".INSTALL", ".CHANGELOG"):
        (stage / name).unlink(missing_ok=True)
    normalization = normalize_sdk_metadata(stage, Path(previous["stage"]).parent / "sysroot", recipe.name)
    changed = {entry["path"] for entry in normalization}
    for file in info["files"]:
        if file["type"] == "file" and file["path"] not in changed and digest(stage / file["path"]) != file["sha256"]:
            raise RuntimeError("Metadata re-export changed a compiled or unrelated payload: " + file["path"])
    snapshots = work / "export-inputs"
    snapshots.mkdir(exist_ok=True)
    for source in (Path(__file__), project / "src/distro_build/packaging/sdk_metadata.py", recipe.path):
        shutil.copy2(source, snapshots / source.name)
    provenance = {**original, "build_identity": identity,
                  "compilation_build_identity": inputs["compiled_identity"],
                  "packaging": {"operation": "verified-compiled-payload-portable-sdk-metadata-export",
                                **inputs, "snapshot": str(snapshots), "changed": normalization}}
    metadata = {**recipe.package, "depends": list(recipe.dependencies["runtime"]),
                "licenses": [recipe.package["license"]]}
    artifact = export_package(stage, project / "out/packages" / recipe.name / identity, metadata,
                              source_date_epoch=int(info["metadata"]["builddate"][0]), provenance=provenance)
    return {"package": recipe.name, "build_identity": identity, "path": str(artifact.path),
            "sha256": artifact.sha256, "stage": str(stage), "provenance": provenance}

def qualify(recipes, candidates):
    records = []
    for recipe in plan(recipes, ["curl", "libarchive"]):
        records.append(candidates.get(recipe.name) or json.loads((project / "out/state/packages" / f"{recipe.name}.json").read_text()))
    inputs = {"packages": {r["package"]: r["sha256"] for r in records}, "qualifier": digest(__file__)}
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    work = project / "out/work/sdk-consumers" / identity
    work.mkdir(parents=True, exist_ok=True)
    root = work / "sysroot"
    if root.exists():
        shutil.rmtree(root)
    PacmanToolkit(project / "out/native-toolkit/prefix").install(root, [Path(r["path"]) for r in records],
        bootstrap=True, expected_hashes={Path(r["path"]).name: r["sha256"] for r in records})
    bootstrap = project / "out/bootstrap/root"
    shutil.copytree(bootstrap / "usr/include", root / "usr/include", symlinks=True, dirs_exist_ok=True)
    tools = bootstrap / "tools"
    check_bootstrap(project, tools)
    compiler = tools / "pass2/bin/x86_64-custom-linux-gnu-gcc"
    if not compiler.is_file() or not (tools / "pass2/.runtime-validated.json").is_file():
        raise RuntimeError("SDK consumer requires the validated source-built pass2 compiler")
    wrapper = work / "target-cc"
    _link_wrapper(wrapper, compiler, root, [root, tools, work])
    (work / "tmp").mkdir(exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TMPDIR": str(work / "tmp"),
           "PKG_CONFIG_PATH": "", "PKG_CONFIG_SYSROOT_DIR": str(root),
           "PKG_CONFIG_LIBDIR": str(root / "usr/lib/pkgconfig") + ":" + str(root / "usr/share/pkgconfig")}
    probes = []
    for name, module, source, static in (
        ("archive", "libarchive", '#include <archive.h>\n#include <stdio.h>\nint main(void){puts(archive_version_details());return 0;}\n', True),
        ("curl", "libcurl", '#include <curl/curl.h>\n#include <stdio.h>\nint main(void){CURL *c=curl_easy_init();if(!c)return 2;puts(curl_version());curl_easy_cleanup(c);return 0;}\n', False)):
        cflags = shlex.split(command(["pkg-config", "--cflags", module], env))
        libs = shlex.split(command(["pkg-config", "--static", "--libs", module], env))
        source_file, obj, binary = work / (name + ".c"), work / (name + ".o"), work / name
        source_file.write_text(source)
        command([wrapper, "-O2", "-c", source_file, "-o", obj, *cflags], env)
        link = command([wrapper, "-static" if static else "-static-libgcc", "-Wl,--verbose", obj, *libs, "-o", binary], env)
        (work / (name + "-link.log")).write_text(link)
        opened = []
        for value in re.findall(r"attempt to open (.+) succeeded", link):
            path = Path(value)
            if path.is_absolute() and not any(path.resolve().is_relative_to(p.resolve()) for p in (root, tools, work)):
                raise RuntimeError("SDK consumer linked an undeclared host input: " + value)
            if path.is_absolute() and path.is_file():
                opened.append({"path": str(path), "sha256": digest(path)})
        dynamic = command(["readelf", "-d", binary], env)
        program = command(["readelf", "-l", binary], env)
        if "(RPATH)" in dynamic or "(RUNPATH)" in dynamic:
            raise RuntimeError("SDK consumer contains a library search path")
        if static:
            if "INTERP" in program or "(NEEDED)" in dynamic or not any(p["path"].endswith("libarchive.a") for p in opened):
                raise RuntimeError("libarchive consumer was not actually statically linked")
            output = command([binary], env)
        else:
            loader = [root / "usr/lib/ld-linux-x86-64.so.2", "--inhibit-cache", "--library-path", root / "usr/lib"]
            listing = command([*loader, "--list", binary], env)
            for line in listing.splitlines():
                if "linux-vdso" in line:
                    continue
                match = re.search(r"(?:=>\s*)?(/[^\s]+)\s+\(0x[0-9a-f]+\)", line)
                if not match or not Path(match[1]).resolve().is_relative_to(root):
                    raise RuntimeError("SDK consumer loaded an undeclared host library: " + line)
            (work / "curl-loader-list.txt").write_text(listing)
            output = command([*loader, binary], env)
        (work / (name + "-probe.log")).write_text(output)
        probes.append({"module": module, "pkg_config_static_flags": libs, "fully_static": static,
                       "binary_sha256": digest(binary), "linker_inputs": opened, "output": output})
    cc = command(["/bin/sh", root / "usr/bin/curl-config", "--cc"], env).strip()
    disabled_static = subprocess.run(["/bin/sh", str(root / "usr/bin/curl-config"), "--static-libs"], env=env,
                                     capture_output=True, text=True)
    if cc != "cc" or disabled_static.returncode != 1 or "static libraries disabled" not in disabled_static.stderr:
        raise RuntimeError("curl-config compiler/static capability metadata is inaccurate")
    report = {"success": True, "identity": identity, "inputs": inputs, "probes": probes,
              "compiler_sha256": digest(compiler),
              "compiler_validation_sha256": digest(tools / "pass2/.runtime-validated.json"),
              "headers_validation_sha256": digest(bootstrap / ".bootstrap-validated.json"),
              "curl_config_cc": cc, "curl_full_static_supported": False,
              "scope": "Fully static source-built libarchive consumer; libcurl pkg-config --static dependency flags used with shared libcurl; own target loader only; unchanged compiled package payloads"}
    output = project / "out/qualification/sdk-consumers" / identity
    output.mkdir(parents=True, exist_ok=True)
    receipt = output / "report.json"
    receipt.write_text(json.dumps(report, indent=2) + "\n")
    return receipt

def main():
    recipes = catalog(project)
    locks = []
    try:
        for name in ("curl", "libarchive"):
            lock = (project / "out/state/locks" / (name + ".lock")).open("a")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            locks.append(lock)
        candidates = {name: prepare(name, recipes[name]) for name in ("curl", "libarchive")}
        receipt = qualify(recipes, candidates)
        for name, record in candidates.items():
            state = project / "out/state/packages" / (name + ".json")
            temporary = state.with_suffix(".tmp")
            record["metadata_qualification"] = {"path": str(receipt), "sha256": digest(receipt)}
            temporary.write_text(json.dumps(record, indent=2) + "\n")
            temporary.replace(state)
        print(json.dumps({"packages": {name: {"path": record["path"], "sha256": record["sha256"]}
                                     for name, record in candidates.items()}, "qualification": str(receipt)}, indent=2))
    finally:
        for lock in locks:
            lock.close()

if __name__ == "__main__":
    main()
