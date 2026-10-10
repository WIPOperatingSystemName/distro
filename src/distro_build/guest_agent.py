"""Bounded QEMU Guest Agent client for private development VM sockets."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import secrets
import socket
import time


class GuestAgent:
    def __init__(self, path: Path, *, timeout: float = 120):
        self.timeout = timeout
        self.buffer = b""
        self.sequence = 0
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(min(timeout, 10))
        try:
            self.socket.connect(str(path))
            token = secrets.randbits(53)
            self.socket.sendall(b"\xff")
            self.call("guest-sync-delimited", {"id": token}, expected=token)
        except OSError as error:
            self.close()
            raise RuntimeError(f"Development guest agent is unavailable at {path}; boot a desktop-dev image for this VM ({error})") from error
        except BaseException:
            self.close()
            raise

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def call(self, command: str, arguments: dict | None = None, *, expected=None):
        self.sequence += 1
        sequence = self.sequence
        request = {"execute": command, "id": sequence}
        if arguments is not None:
            request["arguments"] = arguments
        self.socket.settimeout(min(self.timeout, 10))
        self.socket.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        deadline = time.monotonic() + min(self.timeout, 10)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Guest agent timed out during {command}")
            self.socket.settimeout(remaining)
            if b"\n" not in self.buffer:
                chunk = self.socket.recv(65536)
                if not chunk:
                    raise RuntimeError("Guest agent disconnected")
                self.buffer += chunk
                if len(self.buffer) > 1024 * 1024:
                    raise RuntimeError("Guest agent response exceeds 1 MiB")
                continue
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.rsplit(b"\xff", 1)[-1]
            try:
                response = json.loads(line)
            except (ValueError, UnicodeError):
                if expected is not None:
                    continue  # discard a partial response left by an older client
                raise RuntimeError("Invalid guest agent response")
            if not isinstance(response, dict):
                raise RuntimeError("Invalid guest agent response")
            if expected is not None:
                if response.get("return") == expected:
                    return expected
                if response.get("id") != sequence:
                    continue
            elif response.get("id") != sequence:
                continue
            if "error" in response:
                raise RuntimeError(f"Guest agent {command}: {response['error']}")
            if "return" not in response:
                raise RuntimeError("Guest agent response has no result")
            return response["return"]

    def execute(self, executable: str, arguments: list[str], *, check: bool = True) -> dict:
        process = self.call("guest-exec", {"path": executable, "arg": arguments, "capture-output": True})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            result = self.call("guest-exec-status", {"pid": process["pid"]})
            if result.get("exited"):
                for field in ("out-data", "err-data"):
                    result[field] = base64.b64decode(result.get(field, ""), validate=True).decode(errors="replace")
                if check and (result.get("exitcode") != 0 or result.get("signal")):
                    raise RuntimeError(f"Guest command {executable} failed: {result['err-data'] or result['out-data']} (status {result.get('exitcode')}, signal {result.get('signal')})")
                return result
            time.sleep(0.1)
        raise RuntimeError(f"Guest command {executable} timed out; it may still be running")

    def transfer(self, source: Path, destination: str):
        handle = self.call("guest-file-open", {"path": destination, "mode": "wb"})
        try:
            with source.open("rb") as stream:
                while chunk := stream.read(128 * 1024):
                    while chunk:
                        result = self.call("guest-file-write", {"handle": handle,
                            "buf-b64": base64.b64encode(chunk).decode()})
                        count = result.get("count")
                        if type(count) is not int or not 0 < count <= len(chunk):
                            raise RuntimeError("Guest agent made no valid file-write progress")
                        chunk = chunk[count:]
            self.call("guest-file-flush", {"handle": handle})
        finally:
            self.call("guest-file-close", {"handle": handle})
