"""Real GNU gettext target tools and private own-loader build generators."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from .desktop_native import DesktopBuild


def prepare_gettext_tools(sysroot: Path, work: Path) -> Path:
    """Wrap packaged gettext without borrowing a host loader, library or rules.

    Callers can add ITS rules through GETTEXTDATADIRS, but only inside their
    private work directory or installed dependency sysroot. Each wrapper keeps
    GNU gettext's genuine catalog/XML processing behavior intact.
    """
    sysroot, work = sysroot.resolve(), work.resolve()
    loader = sysroot / "usr/lib/ld-linux-x86-64.so.2"
    if not loader.is_file():
        raise RuntimeError("gettext generator requires the declared target loader")
    destination = work / "gettext-tools"
    destination.mkdir(parents=True, exist_ok=True)
    receipts = {}
    for name in ("msgfmt", "msgmerge", "xgettext"):
        binary = sysroot / "usr/bin" / name
        if not binary.is_file():
            raise RuntimeError(f"source-built target gettext tool missing: {binary}")
        wrapper = destination / name
        wrapper.write_text(
            "#!/usr/bin/python3\nimport os,pathlib,sys\n"
            f"root=pathlib.Path({str(sysroot)!r})\nwork=pathlib.Path({str(work)!r})\n"
            "env=dict(os.environ)\n"
            "for key in ('LD_LIBRARY_PATH','LD_PRELOAD','LD_AUDIT'): env.pop(key,None)\n"
            "extra=env.get('GETTEXTDATADIRS','').split(os.pathsep)\n"
            "for value in filter(None,extra):\n"
            "    path=pathlib.Path(value).resolve()\n"
            "    if not (path.is_relative_to(root) or path.is_relative_to(work)):\n"
            "        sys.exit('Gettext generator refused host ITS rule path: '+value)\n"
            "env['GETTEXTDATADIR']=str(root/'usr/share/gettext')\n"
            "env['GETTEXTDATADIRS']=os.pathsep.join([str(root/'usr/share/gettext'),*filter(None,extra)])\n"
            "env['XDG_DATA_DIRS']=str(root/'usr/share')\n"
            f"binary={str(binary)!r}\nloader={str(loader)!r}\n"
            "os.execve(loader,[loader,'--library-path',str(root/'usr/lib'),binary,*sys.argv[1:]],env)\n")
        wrapper.chmod(0o755)
        receipts[name] = {"binary": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                          "wrapper_sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest()}
    (destination / "receipt.json").write_text(json.dumps({"kind": "packaged-target-tools-own-loader",
        "loader": {"path": str(loader), "sha256": hashlib.sha256(loader.read_bytes()).hexdigest()},
        "tools": receipts}, indent=2) + "\n")
    return destination


def build_gettext() -> None:
    build = DesktopBuild()
    build.autotools(["--disable-static", "--disable-nls", "--disable-rpath", "--disable-java",
                     "--disable-csharp", "--disable-d", "--disable-c++", "--disable-modula2",
                     "--disable-go", "--disable-libasprintf", "--disable-openmp", "--disable-curses",
                     "--disable-acl", "--disable-xattr", "--without-emacs", "--without-git",
                     "--without-selinux", "--without-libsmack", "--with-included-libxml",
                     "--with-included-libunistring", f"--with-sysroot={build.sysroot}"])
    # The optional Python source-editing CLI belongs to a future Python SDK
    # package. Preserve GNU C translation tools and SDK resources without
    # installing a command whose interpreter is absent from this OS closure.
    (build.stage / "usr/bin/spit").unlink(missing_ok=True)
    build.license("gettext", ["COPYING", "gettext-runtime/intl/COPYING.LIB"])
    build.license("gettext/libxml", ["gettext-tools/gnulib-lib/libxml/COPYING"])
    for name in ("msgfmt", "msgmerge", "xgettext"):
        result = build.probe(build.stage / "usr/bin" / name, "--version")
        if " 1.0" not in result:
            raise RuntimeError(f"target gettext {name} version probe failed")
    _verify_merge(build)
    for path in build.stage.rglob("*.la"):
        path.unlink()
    build.audit()


def _verify_merge(build: DesktopBuild) -> None:
    fixture = build.work / "gettext-fixture"
    fixture.mkdir()
    catalog = fixture / "fr.po"
    catalog.write_text('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
                       '"Language: fr\\n"\n\nmsgid "Custom Distro"\nmsgstr "Distribution Personnelle"\n')
    template = fixture / "custom.metainfo.xml"
    template.write_text('<?xml version="1.0"?><component type="desktop-application">'
                        '<id>org.custom.Distro</id><name>Custom Distro</name></component>\n')
    output = fixture / "merged.xml"
    loader = build.sysroot / "usr/lib/ld-linux-x86-64.so.2"
    env = dict(build.env)
    env.update({"GETTEXTDATADIR": str(build.stage / "usr/share/gettext"),
                "GETTEXTDATADIRS": str(build.stage / "usr/share/gettext"),
                "XDG_DATA_DIRS": str(build.stage / "usr/share")})
    base = [str(loader), "--library-path", f"{build.stage}/usr/lib:{build.sysroot}/usr/lib"]
    subprocess.run([*base, str(build.stage / "usr/bin/msgfmt"), "--xml", "--template=" + str(template),
                    "-l", "fr", str(catalog), "-o", str(output)], env=env, check=True)
    nodes = ET.parse(output).getroot().findall("name")
    if not any(node.attrib.get("{http://www.w3.org/XML/1998/namespace}lang") == "fr"
               and node.text == "Distribution Personnelle" for node in nodes):
        raise RuntimeError("real msgfmt XML merge did not preserve the translated name")
    desktop = fixture / "custom.desktop"
    desktop.write_text("[Desktop Entry]\nType=Application\nName=Custom Distro\nExec=/bin/true\n")
    desktop_output = fixture / "merged.desktop"
    subprocess.run([*base, str(build.stage / "usr/bin/msgfmt"), "--desktop", "--template=" + str(desktop),
                    "-l", "fr", str(catalog), "-o", str(desktop_output)], env=env, check=True)
    if "Name[fr]=Distribution Personnelle" not in desktop_output.read_text():
        raise RuntimeError("real msgfmt desktop merge did not preserve the translated name")
    print("CUSTOM_GETTEXT_XML_DESKTOP_MERGE_OK", flush=True)
