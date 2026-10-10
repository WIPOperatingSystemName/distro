#!/usr/bin/env python3
"""Measure real hover rendering in a private development VM after pacman deployment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from distro_build.deploy import vm_directory
from distro_build.guest_agent import GuestAgent
from distro_build.vm import Qmp

DROPIN = "/etc/systemd/user/telorgon-shell.service.d/90-hover-check.conf"
LOG = "/home/custom/.local/state/telorgon/shell.log"
PACKAGES = ["telorgon-shell", "telorgon-file-explorer", "telorgon-settings-app", "telorgon-portal-picker"]
USER = ["--user", "--machine=custom@.host"]


def restart(agent: GuestAgent) -> None:
    agent.execute("/usr/bin/systemctl", [*USER, "daemon-reload"])
    agent.execute("/bin/rm", ["-f", "/run/user/1000/telorgon-wayland-socket"])
    agent.execute("/usr/bin/systemctl", ["restart", "custom-distro-desktop.service"])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        ready = agent.execute("/bin/sh", ["-ec", "read -r socket < /run/user/1000/telorgon-wayland-socket && test -S \"$socket\""], check=False)
        if ready.get("exitcode") == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("Compositor did not publish a ready socket")


def log(agent: GuestAgent) -> str:
    return agent.execute("/bin/busybox", ["tail", "-c", "262144", LOG])["out-data"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "measure", "cleanup"])
    parser.add_argument("--name", required=True, help="Running private desktop-dev VM")
    parser.add_argument("--output", type=Path, default=PROJECT / "out/verification/hover")
    parser.add_argument("--point", action="append", default=[], help="Hover target x,y in guest pixels; repeat for enter/leave")
    parser.add_argument("--seconds", type=float, default=8)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(PROJECT / "out"):
        parser.error("Evidence must be under out/")
    points = []
    for value in args.point:
        try:
            x, y = map(int, value.split(","))
        except ValueError:
            parser.error("Each --point must contain two integers: x,y")
        points.append((x, y))
    if args.action == "measure" and (len(points) < 2 or not 3 <= args.seconds <= 60):
        parser.error("Measurement needs at least two points and --seconds between 3 and 60")
    directory = vm_directory(PROJECT, args.name)
    output.mkdir(parents=True, exist_ok=True)
    with GuestAgent(directory / "agent.sock", timeout=90) as agent:
        if args.action == "prepare":
            config = output / "90-hover-check.conf"
            config.write_text("[Service]\nEnvironment=TELORGON_FRAME_STATS=1\nEnvironment=TELORGON_STALL_PROBES=1\n")
            agent.execute("/bin/mkdir", ["-p", str(Path(DROPIN).parent)])
            agent.transfer(config, DROPIN)
            agent.execute("/bin/chmod", ["644", DROPIN])
            restart(agent)
            print("Hover frame statistics enabled; desktop restarted.")
            return
        if args.action == "cleanup":
            agent.execute("/bin/rm", ["-f", DROPIN])
            restart(agent)
            print("Hover frame statistics removed; desktop restarted.")
            return
        qmp = Qmp(directory / "qmp.sock")
        try:
            ppm = output / "before.ppm"
            qmp.execute("screendump", {"filename": str(ppm)})
            width, height = map(int, ppm.read_bytes().split(b"\n", 3)[1].split())
            if any(not (0 <= x < width and 0 <= y < height) for x, y in points):
                parser.error(f"Hover points must lie inside the {width}x{height} guest display")
            state = agent.execute("/usr/bin/systemctl", [*USER, "show", "telorgon-shell.service", "-p", "MainPID", "-p", "ActiveState"])["out-data"]
            status = dict(line.split("=", 1) for line in state.splitlines())
            if status["ActiveState"] != "active" or status["MainPID"] == "0":
                raise RuntimeError("Compositor is not active")
            versions = agent.execute("/usr/bin/pacman", ["-Q", *PACKAGES])["out-data"]
            executable_hash = agent.execute("/bin/busybox", ["sha256sum", f"/proc/{status['MainPID']}/exe"])["out-data"].split()[0]
            before = log(agent)
            started = time.monotonic()
            count = 0
            while time.monotonic() - started < args.seconds:
                x, y = points[count % len(points)]
                qmp.execute("input-send-event", {"events": [
                    {"type": "abs", "data": {"axis": "x", "value": round(x / width * 32767)}},
                    {"type": "abs", "data": {"axis": "y", "value": round(y / height * 32767)}},
                ]})
                count += 1
                time.sleep(0.1)
            time.sleep(0.5)
            after = log(agent)
            final_pid = agent.execute("/usr/bin/systemctl", [*USER, "show", "telorgon-shell.service", "-p", "MainPID", "--value"])["out-data"].strip()
            observed = after[len(before):] if after.startswith(before) else after
            (output / "shell.log").write_text(observed)
            qmp.execute("screendump", {"filename": str(output / "after.png"), "format": "png"})
            intervals = [{"seconds": float(m[0]), "primary_fps": float(m[1]),
                          "render_cpu_avg_ms": float(m[2]), "render_cpu_max_ms": float(m[3])}
                         for m in re.findall(r"seconds=([\d.]+).*?primary_fps=([\d.]+).*?render_cpu_avg_ms=([\d.]+).*?render_cpu_max_ms=([\d.]+)", observed)]
            report = {"kind": "actual-vm-hover-input", "vm": args.name,
                      "vm_state": json.loads((directory / "state.json").read_text()),
                      "kvm": qmp.execute("query-kvm"), "display_pixels": [width, height],
                      "points": points, "input_batches": count, "seconds": args.seconds,
                      "packages": versions, "shell_pid": int(status["MainPID"]),
                      "shell_pid_after": int(final_pid),
                      "running_shell_sha256": executable_hash, "intervals": intervals,
                      "qualification": "Real QEMU pointer enter/leave input and guest compositor timings; screenshot inspection must establish the chosen controls. FPS is guest presentation, not host display scanout."}
            if (directory / "deployment.json").exists():
                report["deployment"] = json.loads((directory / "deployment.json").read_text())
            report["success"] = bool(intervals) and final_pid == status["MainPID"] and "panicked" not in observed
            (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({"success": report["success"], "intervals": intervals, "report": str(output / "result.json")}, indent=2))
            if not report["success"]:
                raise RuntimeError("Missing frame statistics, compositor restart or panic during hover measurement")
        finally:
            qmp.close()


if __name__ == "__main__":
    main()
