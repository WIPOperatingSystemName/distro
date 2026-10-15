#!/usr/bin/env python3
"""Render the component map and architecture reference, or check their coverage."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
import textwrap
import tomllib
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def catalog() -> tuple[list[dict], dict[str, Path], dict[str, dict]]:
    layers = tomllib.loads((ROOT / "docs/architecture-components.toml").read_text())["layers"]
    packages = {}
    for filename in ("package.toml", "application.toml"):
        for path in sorted((ROOT / "packages").glob(f"*/{filename}")):
            name = tomllib.loads(path.read_text())["package"]["name"]
            if name in packages:
                raise ValueError(f"Duplicate package declaration: {name}")
            packages[name] = path.relative_to(ROOT)
    projects = tomllib.loads((ROOT / "source-projects.toml").read_text())["projects"]
    for key, declared in (("packages", packages), ("projects", projects)):
        entries = Counter(name for layer in layers for group in layer["groups"]
                          for name in group.get(key, []))
        missing = sorted(set(declared) - entries.keys())
        unknown = sorted(entries.keys() - set(declared))
        repeated = sorted(name for name, count in entries.items() if count != 1)
        if missing or unknown or repeated:
            raise ValueError(f"{key}: missing={missing}, unknown={unknown}, repeated={repeated}")
    return layers, packages, projects


def component_details(packages: dict[str, Path], projects: dict[str, dict]) -> dict:
    details = tomllib.loads((ROOT / "docs/component-details.toml").read_text())
    policy = {path.relative_to(ROOT).as_posix() for path in (ROOT / "system").rglob("*")
              if path.is_file() or path.is_symlink()}
    for key, declared in (("packages", set(packages)), ("projects", set(projects)), ("policy", policy)):
        entries = details.get(key, {})
        missing, unknown = sorted(declared - entries.keys()), sorted(entries.keys() - declared)
        if missing or unknown:
            raise ValueError(f"{key} explanations: missing={missing}, unknown={unknown}")
        for name, entry in entries.items():
            fields = {"purpose": entry} if key == "policy" else entry
            if not isinstance(fields, dict):
                raise ValueError(f"{key} explanation {name}: expected a table")
            for field in (("purpose",) if key == "policy" else ("kind", "purpose", "use")):
                if not isinstance(fields.get(field), str) or not fields[field].strip():
                    raise ValueError(f"{key} explanation {name}: {field} must be nonempty text")
            parts = fields.get("parts", {})
            if not isinstance(parts, dict):
                raise ValueError(f"{key} explanation {name}: parts must be a table")
            for part, purpose in parts.items():
                if not isinstance(purpose, str) or not purpose.strip():
                    raise ValueError(f"{key} explanation {name}: empty purpose for {part}")
    for key in ("sdk_crates", "sdk_modules"):
        if not details.get(key) or any(not isinstance(value, str) or not value.strip()
                                      for value in details[key].values()):
            raise ValueError(f"{key}: explanations must be nonempty text")
    return details


def profile_membership() -> tuple[dict[str, set[str]], dict[str, str]]:
    sys.path.insert(0, str(ROOT / "src"))
    from distro_build.compose import runtime_packages

    profiles, descriptions = {}, {}
    for path in sorted((ROOT / "profiles").glob("*.toml")):
        profile = tomllib.loads(path.read_text())
        providers = {} if path.stem == "console" else {"libudev": "systemd"}
        profiles[path.stem] = set(runtime_packages(ROOT, profile["packages"], providers=providers))
        descriptions[path.stem] = profile["description"]
    return profiles, descriptions


def table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_reference(layers: list[dict], packages: dict[str, Path], projects: dict[str, dict],
                     details: dict, profiles: dict[str, set[str]], descriptions: dict[str, str]) -> str:
    lines = [
        "<!-- Generated from docs/component-details.toml by tools/render-architecture.py. -->",
        "",
        "## Component reference",
        "",
        "Every declared package and registered source repository has an individual",
        "explanation here. The reference also lists bundled services and helpers, the",
        "SDK's major subsystems, and every distro-owned guest policy source file.",
        "The [boot and assembled system pieces](#boot-and-assembled-system-pieces)",
        "below cover firmware, disk layout and the generated base/session packages.",
        "",
        f"Coverage: **{len(packages)} package recipes, {len(projects)} source repositories and",
        f"{len(details['policy'])} guest policy files**, plus the named pieces bundled inside them.",
        "Exact installed file lists, versions, source pins and transitive Cargo dependencies",
        "remain in package/image manifests, build receipts and the owning Cargo locks.",
        "This reference describes component responsibilities, not qualification of a",
        "particular image or support for every upstream feature.",
        "",
        "Jump to [source repositories](#source-repositories-and-shared-code),",
        "[SDK subsystems](#inside-the-telorgon-sdk),",
        "[bundled pieces](#bundled-services-and-supporting-pieces), or",
        "[guest policy](#distro-owned-guest-policy).",
        "",
        "### What each profile includes",
        "",
        "The counts below are resolved from profile selections and runtime dependencies",
        "using the same resolver as image composition. They count catalog packages; the",
        "generated base/session policy packages and bootloader are described separately.",
        "The systemd-based profiles select systemd as the libudev provider.",
        "",
        "| Profile | Catalog runtime packages | Purpose |",
        "| --- | --- | --- |",
    ]
    for name, members in profiles.items():
        lines.append(f"| [`{name}`](../profiles/{name}.toml) | {len(members)} | {table_cell(descriptions[name])} |")
    lines += [
        "",
        "In the package tables, **all desktop profiles** means `desktop`, `desktop-use`",
        "and `desktop-dev`; **all systemd-based profiles** also includes `systemd`.",
        "**Catalog only** means none of the current profiles select the package into",
        "their runtime closure. Such a recipe can still supply build inputs or an",
        "alternate provider. A package installed in a saved VM can differ from its",
        "original profile; the VM's deployment receipt records those updates.",
        "",
    ]
    desktop_profiles = {name for name in profiles if name.startswith("desktop")}
    systemd_profiles = set(profiles) - {"console"}
    for layer in layers:
        package_groups = [group for group in layer["groups"] if group.get("packages")]
        if not package_groups:
            continue
        lines += [f"### {layer['title']}", ""]
        for group in package_groups:
            lines += [f"#### {group['title']}", "",
                      "| Component and inclusion | What it provides | Role in this distro |",
                      "| --- | --- | --- |"]
            for name in group["packages"]:
                note = details["packages"][name]
                selected = {profile for profile, members in profiles.items() if name in members}
                if not selected:
                    scope = "Catalog only"
                elif selected == set(profiles):
                    scope = "All profiles"
                elif selected == systemd_profiles:
                    scope = "All systemd-based profiles"
                elif selected == desktop_profiles:
                    scope = "All desktop profiles"
                else:
                    scope = ", ".join(f"`{profile}`" for profile in sorted(selected))
                label = f"[`{name}`](../{packages[name].as_posix()})<br>{table_cell(note['kind'])}<br>**{scope}**"
                lines.append(f"| {label} | {table_cell(note['purpose'])} | {table_cell(note['use'])} |")
            lines.append("")
    lines += ["### Source repositories and shared code", "",
              "These repositories own the implementations. An application source repository",
              "and its package are two views of the same software, rather than two running",
              "services. The SDK is linked into its consumers; the bootloader runs before Linux.",
              "", "| Source repository | What it owns | Distro integration |", "| --- | --- | --- |"]
    for name, project in projects.items():
        note = details["projects"][name]
        url = project["repository"].removesuffix(".git")
        lines.append(f"| [`sources/{name}`]({url})<br>{table_cell(note['kind'])} | {table_cell(note['purpose'])} | {table_cell(note['use'])} |")
    sdk_url = projects["telorgon"]["repository"].removesuffix(".git")
    branch = projects["telorgon"]["branch"]
    lines += ["", "### Inside the Telorgon SDK", "",
              "These are source/library responsibilities inside the shared framework, not",
              "additional guest daemons. Cargo features and host selection determine which",
              "capabilities a particular consumer includes. The distro desktop uses the",
              "CPU/KMS rendering path; SDK support for other hosts/renderers does not add",
              "those runtimes to this image.", "", "| Workspace crate | Purpose |", "| --- | --- |"]
    for name, purpose in details["sdk_crates"].items():
        lines.append(f"| [`{name}`]({sdk_url}/tree/{branch}/crates/{name}) | {table_cell(purpose)} |")
    lines += ["", "| SDK subsystem | Responsibility |", "| --- | --- |"]
    for name, purpose in details["sdk_modules"].items():
        lines.append(f"| [`{name}`]({sdk_url}/tree/{branch}/crates/telorgon/src/{name}) | {table_cell(purpose)} |")
    lines += ["", "### Bundled services and supporting pieces", "",
              "A package can contain several independently named services, libraries or",
              "tools. The following entries explain the main pieces behind the package",
              "names; they are already accounted for by their owning package or provider.", ""]
    for layer in layers:
        for group in layer["groups"]:
            for name in group.get("packages", []):
                parts = details["packages"][name].get("parts", {})
                if not parts:
                    continue
                lines += [f"#### Pieces in {name}", "",
                          f"Owner: [`{name}`](../{packages[name].as_posix()}).", "",
                          "| Piece | Purpose |", "| --- | --- |"]
                lines += [f"| {table_cell(part)} | {table_cell(purpose)} |" for part, purpose in parts.items()]
                lines.append("")
    lines += ["### Distro-owned guest policy", "",
              "The distro layers configuration and session policy onto the source-built",
              "packages. These source files enter the generated base/session packages or",
              "early userspace; they are not independent applications.", "",
              "The `systemd` tree is the common service/session policy. Desktop profiles",
              "also use the `desktop` tree; normal `desktop-use` and `desktop-dev` images",
              "then remove qualification checks and apply the `desktop-use` overlay.",
              "The default `desktop-dev` login flow applies `system/system-shell` last and replaces",
              "the automatic user desktop activation with a dedicated system compositor. It",
              "renders login and the user desktop in the same process. A separate root",
              "manager authenticates with PAM and retains the user's logind session. The",
              "`dev-password` command enrolls the private guest account through PAM, with",
              "the new password on stdin and no credential in images or reports. The",
              "user helper discovers and launches applications under the user's UID; the",
              "compositor admits that user's Wayland clients and revokes them on logout.",
              "A private session connection lets the user helper control display previews;",
              "confirmed preferences stay in the user home and login restores them. Logout",
              "resets the login display and background. The helper validates and decodes",
              "wallpaper in the user context and sends immutable pixels to the renderer;",
              "the renderer does not read user homes. Sound observation and saved preferences",
              "belong to the user helper; audio servers it starts share the logind scope and",
              "end with the session. Reconnects use bounded service restarts.",
              "The helper supervises user portal processes in that scope. Settings imports",
              "photos through the user's FileChooser; system shell ScreenCast is disabled",
              "until a separate session bridge is implemented.",
              "Rows marked qualification describe the disposable check profiles, rather",
              "than services silently running in the normal desktop.", ""]
    for title, prefix in (("Early boot and base identity", "system/"),
                          ("System and login policy", "system/systemd/"),
                          ("Desktop and qualification policy", "system/desktop/"),
                          ("Normal desktop session policy", "system/desktop-use/"),
                          ("Independent system shell policy", "system/system-shell/")):
        rows = [(path, purpose) for path, purpose in sorted(details["policy"].items())
                if path.startswith(prefix) and (prefix != "system/" or len(Path(path).parts) == 2)]
        lines += [f"#### {title}", "", "| Policy source | Purpose |", "| --- | --- |"]
        lines += [f"| [`{path}`](../{path}) | {table_cell(purpose)} |" for path, purpose in rows]
        lines.append("")
    return "\n".join(lines)


def update_reference(content: str, reference: str) -> str:
    start, end = "<!-- BEGIN COMPONENT REFERENCE -->", "<!-- END COMPONENT REFERENCE -->"
    if content.count(start) != 1 or content.count(end) != 1:
        raise ValueError("architecture.md must have exactly one component reference marker pair")
    before, remaining = content.split(start)
    _, after = remaining.split(end)
    return before + start + "\n\n" + reference + "\n" + end + after


def element(parent: ET.Element, tag: str, **attrs: object) -> ET.Element:
    return ET.SubElement(parent, f"{{{SVG_NS}}}{tag}", {
        key.replace("_", "-"): str(value) for key, value in attrs.items()
    })


def text(parent: ET.Element, x: int, y: int, content: str, *, size: int = 15,
         color: str = "#334155", weight: int = 400, mono: bool = False) -> None:
    node = element(parent, "text", x=x, y=y, fill=color, font_size=size,
                   font_weight=weight, font_family="DejaVu Sans Mono, monospace" if mono
                   else "DejaVu Sans, sans-serif")
    node.text = content


def render_svg(layers: list[dict], package_count: int, project_count: int) -> str:
    width, margin, gap = 1200, 32, 16
    content_width = width - 2 * margin
    positions = []
    y = 160
    for layer in layers:
        y += 64
        rows = []
        groups = layer["groups"]
        for offset in range(0, len(groups), 3):
            row = []
            row_groups = groups[offset:offset + 3]
            card_width = (content_width - (len(row_groups) - 1) * gap) // len(row_groups)
            for group in row_groups:
                notes = textwrap.wrap(group["description"], width=(card_width - 32) // 8)
                entries = [("P", name) for name in group.get("packages", [])]
                entries += [("S", f"sources/{name}") for name in group.get("projects", [])]
                height = 76 + len(notes) * 18 + len(entries) * 22
                row.append((group, notes, entries, height))
            row_height = max(card[3] for card in row)
            rows.append((y, row_height, card_width, row))
            y += row_height + gap
        positions.append((layer, rows))
        y += 12
    height = y + 54
    svg = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}",
        "role": "img", "aria-labelledby": "title description",
    })
    element(svg, "title", id="title").text = "Custom Distro component architecture"
    element(svg, "desc", id="description").text = (
        f"All {package_count} declared packages and {project_count} source projects, grouped "
        "by function from applications to the Linux kernel. Boot, development and alternate "
        "components are shown separately. See components.md for linked text equivalents."
    )
    element(svg, "rect", width=width, height=height, fill="#f8fafc")
    text(svg, margin, 48, "Custom Distro", size=30, weight=700, color="#0f172a")
    text(svg, margin, 78, "Component architecture · from applications to the Linux kernel", size=19)
    text(svg, margin, 108, f"{package_count} package recipes · {project_count} source projects · grouped by function", size=15)
    text(svg, margin, 137, "P = package recipe     S = source repository     Placement shows responsibility, not every dependency.", size=14)
    for index, (layer, rows) in enumerate(positions, start=1):
        top = rows[0][0] - 64
        color = layer["color"]
        element(svg, "rect", x=margin, y=top, width=5, height=43, rx=2, fill=color)
        text(svg, margin + 17, top + 20, f"{index:02d}  {layer['title']}", size=20,
             color=color, weight=700)
        text(svg, margin + 17, top + 43, layer["description"], size=14)
        for row_y, row_height, card_width, row in rows:
            for column, (group, notes, entries, _) in enumerate(row):
                x = margin + column * (card_width + gap)
                element(svg, "rect", x=x, y=row_y, width=card_width,
                        height=row_height, rx=9, fill="#ffffff", stroke="#cbd5e1")
                element(svg, "rect", x=x, y=row_y + 12, width=4,
                        height=row_height - 24, rx=2, fill=color)
                text(svg, x + 16, row_y + 29, group["title"], size=17,
                     color="#0f172a", weight=700)
                line_y = row_y + 53
                for note in notes:
                    text(svg, x + 16, line_y, note, size=14)
                    line_y += 18
                line_y += 14
                for kind, name in entries:
                    badge_color = "#1d4ed8" if kind == "P" else "#7c3aed"
                    text(svg, x + 16, line_y, kind, size=12, color=badge_color, weight=700)
                    text(svg, x + 36, line_y, name, size=14, mono=True)
                    line_y += 22
    text(svg, margin, height - 35, "Catalog view: profiles select installed components; SDK features determine available capabilities.", size=14)
    text(svg, margin, height - 13, "Recipe and source manifests remain authoritative. Linked component index: docs/components.md", size=14)
    ET.indent(svg, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(svg, encoding="unicode") + "\n"


def render_markdown(layers: list[dict], packages: dict[str, Path], projects: dict[str, dict]) -> str:
    lines = [
        "# Component architecture",
        "",
        "<!-- Generated by tools/render-architecture.py; edit docs/architecture-components.toml. -->",
        "",
        "This map places every declared OS package and every registered source repository",
        "by its primary function. Read the [runtime overview](architecture.md#runtime-layers)",
        "first for the relationships between applications, the shell, services and Linux.",
        "For an explanation of each individual component, read the",
        "[component reference](architecture.md#component-reference).",
        "",
        f"Coverage: **{len(packages)} package recipes and {len(projects)} source projects**.",
        "Kernel subsystems and transitive Cargo dependencies remain inside their owning",
        "components. Repository infrastructure is listed [below](#repository-infrastructure).",
        "",
        "[![Layered map of all package recipes and source projects, grouped by function](diagrams/components.svg)](diagrams/components.svg)",
        "",
        "Open the image for the full-size diagram. The linked tables below provide the",
        "same grouping as text. **P** means a package recipe; **S** means a source repository.",
        "",
        "## Reading the map",
        "",
        "- Layers group responsibilities, rather than requiring calls to pass through",
        "  every layer. Applications and services can use libraries and kernel APIs directly.",
        "- A group can include daemons, libraries, command-line tools and data. Only the",
        "  service programs run as daemons; their libraries are loaded by callers.",
        "- Telorgon is shared Rust SDK code used by both the shell and applications, and",
        "  by the bootloader for boot UI. It is not a separate desktop service. Its",
        "  source workspace contains the SDK, its macro companion and a shader build tool.",
        "- This is the available catalog. [Profiles](../profiles) select an installed",
        "  runtime closure; the map does not claim that every package is in every image.",
        "  Desktop profiles use systemd's udev/libudev provider instead of standalone eudev.",
        "- Boot handoff and development/build components have separate bands. The",
        "  bootloader runs before Linux; the QEMU Guest Agent is for `desktop-dev`.",
        "- Component presence does not establish runtime qualification. In particular,",
        "  the CPU desktop's screen-sharing support depends on the selected SDK capability.",
        "",
        "## Component index",
        "",
    ]
    for layer in layers:
        lines += [f"### {layer['title']}", "", layer["description"], "",
                  "| Function | Components | Responsibility |",
                  "| --- | --- | --- |"]
        for group in layer["groups"]:
            links = [f"P: [`{name}`](../{packages[name].as_posix()})"
                     for name in group.get("packages", [])]
            links += [f"S: [`sources/{name}`]({projects[name]['repository'].removesuffix('.git')})"
                      for name in group.get("projects", [])]
            lines.append(f"| {group['title']} | {'<br>'.join(links)} | {group['description']} |")
        lines.append("")
    lines += [
        "## Repository infrastructure",
        "",
        "These areas define or produce the system; they are outside the running kernel",
        "and userspace stack. Host seed tools and bootstrap generators are build inputs.",
        "",
        "| Area | Role |",
        "| --- | --- |",
        "| [`sources/`](sources.md), [`source-projects.toml`](../source-projects.toml) | Owning component clones and registered source inputs |",
        "| [`packages/`](../packages), [`profiles/`](../profiles) | Package definitions and image/runtime selection |",
        "| [`system/`](../system) | Guest init scripts, service units, session defaults and system policy installed in images |",
        "| [`bootstrap/`](../bootstrap) | Source pins and stages for the cross-toolchain and native build generators |",
        "| [`src/distro_build/`](../src/distro_build), [`build.py`](../build.py), [`run`](../run), [`clean`](../clean), [`pyproject.toml`](../pyproject.toml) | Python coordination for source synchronization, package builds, image composition, VMs, deployment and cleanup |",
        "| [`tools/`](../tools), [`tests/`](../tests) | Qualification utilities, documentation rendering and automated checks |",
        "| [`.github/workflows/`](../.github/workflows), [`.integration/`](../.integration) | CI definitions and integration bundle records |",
        "| [`docs/`](README.md), [`README.md`](../README.md), [`AGENTS.md`](../AGENTS.md), [`LICENSE`](../LICENSE) | Project documentation, contribution rules and licensing |",
        "| `out/` | Generated snapshots, caches, packages, images, private VM state and actual test evidence; see the [artifact inventory](architecture.md#artifact-inventory) |",
        "",
        "Package versions, archive checksums, dependency lists and source origins remain",
        "authoritative in their manifests. This map intentionally records only functional",
        "placement. See [packages](packages.md) for dependency semantics and",
        "[source modules](sources.md) for repository ownership.",
        "",
        "## Updating the map",
        "",
        "When adding or renaming a package or registered source project, update its group",
        "in [`architecture-components.toml`](architecture-components.toml) and its explanation",
        "in [`component-details.toml`](component-details.toml). Guest policy changes also need",
        "an entry there. Then regenerate the map, index and architecture reference:",
        "",
        "```sh",
        "python3 tools/render-architecture.py",
        "python3 tools/render-architecture.py --check",
        "```",
        "",
        "Run these commands from the distro root. The check rejects missing, unknown or",
        "multiply listed components, missing explanations and stale generated documentation.",
        "It runs in CI.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check coverage and generated files without writing")
    args = parser.parse_args()
    try:
        layers, packages, projects = catalog()
        details = component_details(packages, projects)
        profiles, descriptions = profile_membership()
        architecture = ROOT / "docs/architecture.md"
        outputs = {
            ROOT / "docs/diagrams/components.svg": render_svg(layers, len(packages), len(projects)),
            ROOT / "docs/components.md": render_markdown(layers, packages, projects),
            architecture: update_reference(architecture.read_text(),
                render_reference(layers, packages, projects, details, profiles, descriptions)),
        }
        stale = []
        for path, content in outputs.items():
            if args.check:
                if not path.is_file() or path.read_text() != content:
                    stale.append(str(path.relative_to(ROOT)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        if stale:
            raise ValueError(f"Stale component documentation: {', '.join(stale)}; run python3 tools/render-architecture.py")
    except (ValueError, KeyError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"{'Checked' if args.check else 'Rendered'} {len(packages)} packages, "
          f"{len(projects)} source projects and {len(details['policy'])} guest policy files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
