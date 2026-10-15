"""Explicit password enrollment in the private system-shell development guest."""
from __future__ import annotations

import fcntl
import getpass
import json
from pathlib import Path
import sys
import unicodedata

from .deploy import vm_directory
from .guest_agent import GuestAgent


def validate_password(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if not 8 <= len(encoded) <= 512:
        raise ValueError("Use a password between 8 and 512 UTF-8 bytes")
    if any(unicodedata.category(character) == "Cc" for character in password):
        raise ValueError("Passwords cannot contain control characters")
    return encoded


def prompt_password() -> str:
    if not sys.stdin.isatty():
        raise RuntimeError("Run dev-password in an interactive terminal; passwords are not accepted as arguments")
    password = getpass.getpass("New password for custom: ")
    validate_password(password)
    if password != getpass.getpass("Confirm password: "):
        raise ValueError("Passwords do not match")
    return password


def enroll_password(project: Path, *, name: str, password: str) -> dict:
    encoded = validate_password(password)
    directory = vm_directory(project.resolve(), name)
    with (directory / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with GuestAgent(directory / "agent.sock", timeout=30) as guest:
            manifest = json.loads(guest.execute("/bin/cat", ["/etc/custom-distro/build-manifest.json"])["out-data"])
            if manifest.get("profile") != "experimental-desktop-dev" or manifest.get("system_shell") is not True:
                raise RuntimeError("dev-password requires a desktop-dev VM with the system login shell")
            # PAM owns password policy, hashing, locking and atomic shadow updates.
            # Neither the password nor its hash enters arguments or saved output.
            guest.execute("/usr/bin/telorgon-test-shell", ["--set-user-password", "custom"],
                          input_data=encoded + b"\n", capture_output=False)
    return {"vm": name, "user": "custom", "password_enrolled": True,
            "next_step": "Select custom in the VM login view and enter your password"}
