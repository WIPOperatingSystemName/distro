#!/usr/bin/env python3
"""Check one actual app's client log for a configured, SHM-mapped window."""
import argparse
import hashlib
import json
from pathlib import Path
import re

def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()

def check_vm(vm_report):
    record = json.loads(vm_report.read_text())
    image = Path(record["image"])
    if digest(image) != record["image_sha256"]:
        raise ValueError("VM image differs from the actual boot receipt")
    serial = Path(record["serial_log"])
    if serial.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("VM serial log exceeds the bounded qualification size")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", serial.read_text(errors="replace"))
    lines = []
    for line in text.replace("\r", "").splitlines():
        # journalctl short output may prefix a unit/PID before the actual line.
        prefix = re.search(r"\b[A-Za-z0-9_.@-]+\[\d+\]:\s+(.*)$", line)
        lines.append(prefix[1] if prefix else line)
    blocks = {}
    active = None
    for line in lines:
        begin = re.fullmatch(r"CUSTOM_WAYLAND_LOG_BEGIN name=(files|settings)", line.strip())
        end = re.fullmatch(r"CUSTOM_WAYLAND_LOG_END name=(files|settings)", line.strip())
        if begin:
            if active is not None or begin[1] in blocks:
                raise ValueError("Duplicate or nested Wayland log blocks")
            active = begin[1]
            blocks[active] = []
        elif end:
            if active != end[1]:
                raise ValueError("Mismatched Wayland log block boundary")
            active = None
        elif active:
            blocks[active].append(line)
    if active is not None or set(blocks) != {"files", "settings"}:
        raise ValueError("Completed files and settings Wayland log blocks are required")
    apps = [check("\n".join(blocks[name]), app_id, title=title, evidence="mapped-shm")
            for name, app_id, title in (("files", "org.telorgon.FileExplorer", "Telorgon Files"),
                                       ("settings", "org.telorgon.settings", "Telorgon Settings"))]
    input_marker = "CUSTOM_DESKTOP_WINDOW_INPUT_OK configured=1 initial_presented=1 keyboard_focus=1 key_pressed=1 redraw_presented=1"
    input_proved = input_marker in [line.strip() for line in lines]
    return {"passed": bool(record.get("success") and record.get("desktop_input_sent") and input_proved and
                            all(app["passed"] for app in apps)),
            "kind": "actual-vm-app-window-and-input-qualification", "image": str(image),
            "image_sha256": record["image_sha256"], "vm_report": str(vm_report.resolve()),
            "vm_report_sha256": digest(vm_report), "serial_log": str(serial), "serial_sha256": digest(serial),
            "parser_sha256": digest(__file__), "apps": apps, "input_presentation_proved": input_proved,
            "input_sent_by_qemu": record.get("desktop_input_sent", False), "vm_check_passed": record.get("success", False),
            "scope": "Actual boot receipt and exact image SHA; both title-identified configured app windows submitted SHM buffers, entered an output and received matching buffer releases. App frame/presentation feedback is not inferred; the independent C probe proves keyboard input and compositor presentation/redraw feedback."}

def check(text, app_id, *, title=None, evidence="surface-frame"):
    # libwayland 1.24 uses '#'; older C and Rust backends use '@'. A display
    # sync callback never proves a surface frame. The source-inspected VM apps
    # currently omit app_id and surface.frame, so their explicit alternative
    # evidence is title + configure + SHM commit + output entry + buffer release.
    if evidence not in {"surface-frame", "mapped-shm"}:
        raise ValueError("Unknown window evidence requirement")
    pattern = re.compile(r"(\w+)[@#](\d+)\.(\w+)\s*,?\s*\((.*)\)\s*$")
    events = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if "[discarded]" in line:
            continue
        match = pattern.search(line)
        if match:
            events.append({"line": line_number, "interface": match[1], "object": int(match[2]),
                           "method": match[3], "args": match[4]})
    refs = lambda event, interface: [int(value) for value in re.findall(r"\b" + re.escape(interface) + r"[@#](\d+)", event["args"])]
    xdg_to_surface, top_to_xdg, identities, titles = {}, {}, {}, {}
    for event in events:
        if event["method"] == "get_xdg_surface" and refs(event, "xdg_surface") and refs(event, "wl_surface"):
            xdg_to_surface[refs(event, "xdg_surface")[0]] = refs(event, "wl_surface")[0]
        if event["interface"] == "xdg_surface" and event["method"] == "get_toplevel" and refs(event, "xdg_toplevel"):
            top_to_xdg[refs(event, "xdg_toplevel")[0]] = event["object"]
        if event["interface"] == "xdg_toplevel" and event["method"] in {"set_app_id", "set_title"}:
            value = re.search(r'"([^"\\]*(?:\\.[^"\\]*)*)"', event["args"])
            if value:
                (identities if event["method"] == "set_app_id" else titles)[event["object"]] = value[1]
    selected = [top for top in top_to_xdg if identities.get(top) == app_id or
                (title is not None and top not in identities and titles.get(top) == title)]
    candidates = []
    for top in selected:
        xdg = top_to_xdg.get(top)
        surface = xdg_to_surface.get(xdg)
        if surface is None:
            continue
        relevant = [event for event in events if (event["interface"], event["object"]) == ("xdg_surface", xdg)]
        configurations = {}
        ack = None
        for event in relevant:
            numbers = re.findall(r"\b\d+\b", event["args"])
            if not numbers:
                continue
            serial = int(numbers[0])
            if event["method"] == "configure":
                configurations[serial] = event["line"]
            elif event["method"] == "ack_configure" and serial in configurations and configurations[serial] < event["line"]:
                ack = event["line"]
                break
        if ack is None:
            continue
        creations = [(ref, event["line"]) for event in events if event["interface"] == "wl_shm_pool" and
                     event["method"] == "create_buffer" for ref in refs(event, "wl_buffer")]
        attach = buffer = creation_line = None
        commits = []
        for event in events:
            if event["interface"] != "wl_surface" or event["object"] != surface or event["line"] <= ack:
                continue
            if event["method"] == "attach":
                referenced = refs(event, "wl_buffer")
                prior = [(ref, line) for ref, line in creations if ref in referenced and line < event["line"]]
                if not prior:
                    attach = buffer = creation_line = None
                    continue
                buffer, creation_line = max(prior, key=lambda item: item[1])
                attach = event["line"]
            if event["method"] == "commit" and attach is not None and event["line"] > attach:
                commits.append((attach, buffer, creation_line, event["line"]))
        for attach, buffer, creation_line, commit in commits:
            # Numeric IDs can be reused after destruction. Never associate a
            # release from a later wl_buffer generation with this attachment.
            next_creation = min((line for ref, line in creations if ref == buffer and line > creation_line),
                                default=float("inf"))
            releases = [event["line"] for event in events if event["interface"] == "wl_buffer" and
                        event["object"] == buffer and event["method"] == "release" and commit < event["line"] < next_creation]
            entries = [event["line"] for event in events if event["interface"] == "wl_surface" and
                       event["object"] == surface and event["method"] == "enter" and event["line"] > commit]
            callbacks = {ref: event["line"] for event in events if event["interface"] == "wl_surface" and
                         event["object"] == surface and event["method"] == "frame" and ack < event["line"] < commit
                         for ref in refs(event, "wl_callback")}
            callback_expiry = {ref: min((event["line"] for event in events if event["line"] > requested and
                               ref in refs(event, "wl_callback")), default=float("inf"))
                               for ref, requested in callbacks.items()}
            done = [event["line"] for event in events if event["interface"] == "wl_callback" and event["method"] == "done" and
                    event["object"] in callbacks and commit < event["line"] < callback_expiry[event["object"]]]
            qualified = bool(done) if evidence == "surface-frame" else bool(releases and entries)
            if qualified:
                candidates.append({"xdg_toplevel": top, "xdg_surface": xdg, "wl_surface": surface,
                                   "identity_method": "set_app_id" if top in identities else "set_title",
                                   "app_id_observed": identities.get(top), "title_observed": titles.get(top),
                                   "ack_line": ack, "shm_buffer": buffer, "shm_create_line": creation_line,
                                   "shm_attach_line": attach, "buffer_commit_line": commit,
                                   "buffer_release_line": releases[0] if releases else None,
                                   "output_enter_line": entries[0] if entries else None,
                                   "surface_callback_done_line": done[0] if done else None,
                                   "surface_frame_callback_proved": bool(done)})
                break
    return {"passed": bool(candidates), "app_id": app_id, "windows": candidates,
            "required_evidence": evidence,
            "scope": ("configured xdg surface with genuine SHM buffer attachment/commit and matching surface frame callback; scanout pixels and input are qualified separately"
                      if evidence == "surface-frame" else "source-inspected app title, configured xdg surface, genuine SHM attachment/commit, output entry and matching buffer release; release means the compositor no longer uses that buffer and does not prove scanout presentation")}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, nargs="?")
    parser.add_argument("--app-id")
    parser.add_argument("--title", help="Explicit source-inspected title identity when the client omits app_id")
    parser.add_argument("--evidence", choices=("surface-frame", "mapped-shm"), default="surface-frame")
    parser.add_argument("--vm-report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.vm_report:
        if args.log or args.app_id or args.title or args.evidence != "surface-frame":
            parser.error("--vm-report selects both actual app log blocks")
        try:
            report = check_vm(args.vm_report)
        except (ValueError, KeyError, OSError) as error:
            report = {"passed": False, "error": str(error)}
    else:
        if not args.log or not args.app_id:
            parser.error("Supply a log and --app-id, or --vm-report")
        content = args.log.read_bytes()
        report = check(content.decode("utf-8", "replace"), args.app_id, title=args.title, evidence=args.evidence)
        report.update({"log": str(args.log.resolve()), "log_sha256": hashlib.sha256(content).hexdigest()})
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
