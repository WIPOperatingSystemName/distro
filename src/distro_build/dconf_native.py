"""Real dconf databases and desktop schemas, built with the private target SDK."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .desktop_services import ServiceBuild


def build_desktop_settings(name: str) -> None:
    b = ServiceBuild()
    b.glib_generators()
    if name == "dconf":
        bundled = b.source / "subprojects/gvdb"
        (b.work / "bundled-gvdb.json").write_text(json.dumps({
            "origin": "complete GVDB sources in the verified official dconf release archive",
            "files": {str(p.relative_to(bundled)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(bundled.rglob("*")) if p.is_file()}}, indent=2) + "\n")
        b.meson(["--wrap-mode=nodownload", "--force-fallback-for=gvdb",
                 "-Dbash_completion=false", "-Dman=false", "-Dgtk_doc=false",
                 "-Dvapi=false", "-Dsystemduserunitdir=/usr/lib/systemd/user"])
        b.license(name, ["COPYING", "subprojects/gvdb/LICENSES/LGPL-2.1-or-later.txt"])
        module_dir = b.stage / "usr/lib/gio/modules"
        b.closed_probe(b.sysroot / "usr/bin/gio-querymodules", str(module_dir))
        if "libdconfsettings.so: gsettings-backend" not in (module_dir / "giomodule.cache").read_text():
            raise RuntimeError("The target GIO module generator did not register the genuine dconf backend")
        fixture = b.work / "desktop-settings-probe"
        sources = fixture / "defaults.d"
        sources.mkdir(parents=True)
        (sources / "desktop").write_text("[org/gnome/desktop/wm/preferences]\n"
                                         "button-layout='appmenu:minimize,maximize,close'\n")
        database = fixture / "defaults"
        b.closed_probe(b.stage / "usr/bin/dconf", "compile", str(database), str(sources))
        profile = fixture / "profile"
        profile.write_text("user-db:user\nfile-db:" + str(database) + "\n")
        b.env.update({"DCONF_PROFILE": str(profile), "XDG_CONFIG_HOME": str(fixture / "config"),
                      "XDG_RUNTIME_DIR": str(fixture), "HOME": str(fixture)})
        fixture.chmod(0o700)
        result = b.closed_probe(b.stage / "usr/bin/dconf", "read",
                                "/org/gnome/desktop/wm/preferences/button-layout")
        if result.strip() != "'appmenu:minimize,maximize,close'":
            raise RuntimeError("The actual target dconf client did not read the private compiled database")
        (b.work / "dconf-private-database-probe.json").write_text(json.dumps({
            "success": True, "compiler": "source-built target dconf", "database_sha256":
            hashlib.sha256(database.read_bytes()).hexdigest(), "value": result.strip(),
            "profile": "user-db:user plus private file-db; no host user settings modified",
            "scope": "exact compile/read path used by Telorgon's managed desktop settings"}, indent=2) + "\n")
        print("CUSTOM_DCONF_PRIVATE_DATABASE_OK", flush=True)
        hook_name, target, command = "update-gio-modules", "usr/lib/gio/modules/*.so", "/usr/bin/gio-querymodules /usr/lib/gio/modules"
    elif name == "gsettings-desktop-schemas":
        b.gettext_generators()
        b.meson(["-Dintrospection=false"])
        b.license(name, ["COPYING"])
        schemas = b.stage / "usr/share/glib-2.0/schemas"
        b.closed_probe(b.sysroot / "usr/bin/glib-compile-schemas", "--strict", str(schemas))
        b.env["GSETTINGS_SCHEMA_DIR"] = str(schemas)
        result = b.closed_probe(b.sysroot / "usr/bin/gsettings", "list-keys", "org.gnome.desktop.wm.preferences")
        if "button-layout" not in result.splitlines():
            raise RuntimeError("The target desktop-schema cache lacks Telorgon's window decoration preference")
        print("CUSTOM_GSETTINGS_DESKTOP_SCHEMA_OK", flush=True)
        hook_name, target, command = "update-gsettings-schemas", "usr/share/glib-2.0/schemas/*.xml", "/usr/bin/glib-compile-schemas /usr/share/glib-2.0/schemas"
    else:
        raise ValueError(name)
    hook = b.stage / "usr/share/libalpm/hooks" / (hook_name + ".hook")
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("[Trigger]\nOperation = Install\nOperation = Upgrade\nOperation = Remove\n"
                    "Type = Path\nTarget = " + target + "\n\n[Action]\n"
                    "Description = Updating desktop settings cache...\nWhen = PostTransaction\n"
                    "Exec = " + command + "\n")
    if name == "dconf":
        b.audit()
    b.audit_payload_paths()
