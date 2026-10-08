"""Source-built MIME data, XML parsing and desktop file dispatch tools."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

from .desktop_services import ServiceBuild


class MimeBuild(ServiceBuild):
    def file_generator(self) -> None:
        """Compile the exact upstream file version in a private host prefix."""
        directory = self.work / "native-file"
        directory.mkdir()
        env = self.host_env()
        options = ["--disable-shared", "--enable-static", "--disable-zlib", "--disable-bzlib",
                   "--disable-xzlib", "--disable-zstdlib", "--disable-lzlib", "--disable-lrziplib",
                   "--disable-libseccomp", "--disable-landlock"]
        self.host_run([str(self.source / "configure"), f"--prefix={self.native}", *options], directory, env)
        self.host_run(["make", "-C", "src", f"-j{self.jobs}"], directory, env)
        binary = directory / "src/file"
        version = subprocess.check_output([str(binary), "--version"], env=env, text=True)
        if not version.startswith("file-5.48\n"):
            raise RuntimeError("Pinned private file generator has an unexpected version")
        wrappers = self.native / "bin"
        wrappers.mkdir(exist_ok=True)
        wrapper = wrappers / "file"
        wrapper.write_text("#!/usr/bin/python3\nimport os,sys\n"
                           f"binary={str(binary)!r}\nos.execv(binary,[binary,*sys.argv[1:]])\n")
        wrapper.chmod(0o755)
        self.env["PATH"] = str(wrappers) + os.pathsep + self.env["PATH"]
        (self.work / "native-file-generator.json").write_text(json.dumps({
            "kind": "source-built-private-host-generator", "same_pinned_source_as_target": True,
            "binary": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "version": version, "declared_host_seeds": ["gcc", "make"],
            "target_payload_contains_host_generator": False}, indent=2) + "\n")

    def verify_shared_mime(self) -> None:
        database = self.stage / "usr/share/mime"
        self.env["XDG_DATA_DIRS"] = str(self.stage / "usr/share")
        data_home = self.work / "mime-data-home"
        data_home.mkdir()
        self.env.update({"XDG_DATA_HOME": str(data_home),
                         "GIO_MODULE_DIR": str(self.sysroot / "usr/lib/gio/modules"),
                         "GIO_USE_VFS": "local"})
        self.closed_probe(self.stage / "usr/bin/update-mime-database", str(database))
        required = ["mime.cache", "globs2", "magic", "types", "subclasses", "aliases"]
        if any(not (database / name).is_file() or not (database / name).stat().st_size for name in required):
            raise RuntimeError("Target update-mime-database did not create the real MIME caches")
        if "image/png" not in (database / "types").read_text():
            raise RuntimeError("Generated MIME database is missing PNG support")
        # Exercise GLib's content guessing against this package's actual cache.
        source = self.work / "mime-probe.c"
        source.write_text('#include <gio/gio.h>\n#include <stdio.h>\n#include <string.h>\n'
            'int main(void){gboolean uncertain; const guchar png[]={137,80,78,71,13,10,26,10};'
            'char *type=g_content_type_guess("image.png",png,sizeof(png),&uncertain);'
            'int ok=type && !strcmp(type,"image/png") && !uncertain;'
            'g_free(type);if(!ok)return 1;puts("CUSTOM_SHARED_MIME_OK");return 0;}\n')
        import shlex
        flags = subprocess.check_output(["pkg-config", "--cflags", "--libs", "gio-2.0"],
                                        env=self.env, text=True)
        binary = self.work / "mime-probe"
        self.run([str(self.wrapper), str(source), "-o", str(binary), "-static-libgcc", *shlex.split(flags)])
        self.closed_probe(binary)
        (self.work / "mime-database-probe.json").write_text(json.dumps({
            "target_cache_generator": True, "glib_content_type_guess": "image/png",
            "host_mime_database_used": False,
            "cache_sha256": hashlib.sha256((database / "mime.cache").read_bytes()).hexdigest()}, indent=2) + "\n")


def build_mime(name: str) -> None:
    build = MimeBuild()
    if name == "libxml2":
        build.meson(["-Ddocs=disabled", "-Dpython=disabled", "-Dicu=disabled", "-Dreadline=disabled",
                     "-Dhistory=disabled", "-Dzlib=disabled", "-Dhttp=disabled", "-Diconv=enabled"])
        build.license(name, ["Copyright"])
        build.closed_probe(build.stage / "usr/bin/xmllint", "--version")
        document = build.work / "xml-probe.xml"
        document.write_text('<custom-distro><package name="mime"/></custom-distro>\n')
        build.closed_probe(build.stage / "usr/bin/xmllint", "--nonet", "--noout", str(document))
    elif name == "shared-mime-info":
        build.gettext_generators()
        build.meson(["-Dupdate-mimedb=false", "-Dbuild-tools=true", "-Dbuild-translations=true",
                     "-Dbuild-tests=false", "-Dbuild-spec=false"], cpp=True)
        build.license(name, ["COPYING", "data/its/LICENSE.md"])
        build.verify_shared_mime()
        hook = build.stage / "usr/share/libalpm/hooks/update-mime-database.hook"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("[Trigger]\nOperation = Install\nOperation = Upgrade\nOperation = Remove\n"
                        "Type = Path\nTarget = usr/share/mime/packages/*.xml\n\n"
                        "[Action]\nDescription = Updating MIME type database...\n"
                        "When = PostTransaction\nExec = /usr/bin/update-mime-database /usr/share/mime\n")
    elif name == "desktop-file-utils":
        build.meson([])
        build.license(name, ["COPYING"])
        # Keep the cache destination present even after the last app is removed.
        (build.stage / "usr/share/applications").mkdir(parents=True, exist_ok=True)
        fixture = build.work / "desktop-probe"
        fixture.mkdir()
        desktop = fixture / "org.custom.Distro.desktop"
        desktop.write_text("[Desktop Entry]\nType=Application\nName=Custom Distro\nExec=/bin/true\nMimeType=text/plain;\n")
        build.closed_probe(build.stage / "usr/bin/desktop-file-validate", str(desktop))
        build.closed_probe(build.stage / "usr/bin/update-desktop-database", str(fixture))
        if "text/plain=org.custom.Distro.desktop;" not in (fixture / "mimeinfo.cache").read_text():
            raise RuntimeError("Target desktop database did not register the test MIME handler")
        print("CUSTOM_DESKTOP_DATABASE_OK", flush=True)
        hook = build.stage / "usr/share/libalpm/hooks/update-desktop-database.hook"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("[Trigger]\nOperation = Install\nOperation = Upgrade\nOperation = Remove\n"
                        "Type = Path\nTarget = usr/share/applications/*.desktop\n\n"
                        "[Action]\nDescription = Updating desktop application database...\n"
                        "When = PostTransaction\nExec = /usr/bin/update-desktop-database /usr/share/applications\n")
    elif name == "file":
        build.file_generator()
        build.autotools(["--disable-static", "--enable-zlib", "--enable-xzlib", "--enable-libseccomp",
                         "--disable-bzlib", "--disable-zstdlib", "--disable-lzlib", "--disable-lrziplib",
                         f"--with-sysroot={build.sysroot}"])
        build.license(name, ["COPYING"])
        database = build.stage / "usr/share/misc/magic.mgc"
        probe = build.work / "content-probe.pdf"
        probe.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n")
        build.env["MAGIC"] = str(database)
        kind = build.closed_probe(build.stage / "usr/bin/file", "--mime-type", "--brief", str(probe))
        if kind.strip() != "application/pdf":
            raise RuntimeError(f"Target file did not identify PDF content: {kind!r}")
        print("CUSTOM_FILE_MIME_OK", flush=True)
    else:
        raise RuntimeError(f"Unknown source-built MIME package: {name}")
    for path in build.stage.rglob("*.la"):
        path.unlink()
    build.audit()
