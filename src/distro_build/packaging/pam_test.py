"""Explicit disposable guest password authentication fixture, using real PAM.

Preparation/staging never execute PAM, a target helper or a host service. The
booted guest runs the package-owned root qualifier and normal-user PAM probe.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil

from .archive import export_package, inspect_package
from .toolkit import PacmanToolkit, ToolkitError

NAME = "custom-distro-pam-test"
UNIT = "custom-distro-pam-authentication-test.service"
CHECK = "usr/libexec/custom-distro/check-pam-authentication"
PROBE = "usr/libexec/custom-distro/pam-auth-probe"
DATA = "usr/share/custom-distro/pam-test"
CREDENTIALS = "home/custom/.local/state/pam-test"
LOCKED_SCOPE = DATA + "/locked-custom-entry.sha256"
SUCCESS = "CUSTOM_PAM_AUTHENTICATION_OK"

UNIT_TEXT = """[Unit]
Description=Disposable real PAM password authentication qualification
Requires=dbus.service
After=dbus.service
Before=custom-distro-system-check.service

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=120
StandardOutput=journal+console
StandardError=journal+console
ExecStart=/usr/libexec/custom-distro/check-pam-authentication

[Install]
WantedBy=multi-user.target
"""

CHECK_SCRIPT = r'''#!/bin/sh
# Disposable VM fixture only. Never enable shell tracing: stdin carries secrets.
set -eu
export PATH=/usr/bin:/bin
umask 077
fail() { echo CUSTOM_PAM_AUTHENTICATION_FAILED; exit 1; }
# Refuse a host context before installing any trap that contacts systemd.
[ "$(id -u)" = 0 ] || fail
grep -qx 'ID=custom-distro' /etc/os-release || fail
[ -f /usr/share/custom-distro/pam-test/fixture.json ] || fail
runtime=/run/custom-distro/pam-test
credentials=/home/custom/.local/state/pam-test
probe=/usr/libexec/custom-distro/pam-auth-probe
# The nonsecret root-owned guest marker must be readable by the actual UID 1000
# probe. Preserve private storage in the child instead of making its parent 0700.
mkdir -p /run/custom-distro
chmod 755 /run/custom-distro
mkdir -p "$runtime"
chmod 700 "$runtime"
backup="$runtime/shadow.original"
restored=0
backup_created=0
original_sha=
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    for unit in custom-distro-pam-locked custom-distro-pam-wrong custom-distro-pam-correct; do
        systemctl stop "$unit.service" >/dev/null 2>&1 || true
    done
    if [ "$backup_created" = 1 ] && [ "$restored" != 1 ]; then
        if cp -p "$backup" /etc/shadow && chmod 600 /etc/shadow &&
           [ "$(sha256sum /etc/shadow | cut -d' ' -f1)" = "$original_sha" ]; then
            restored=1
        else
            result=1
        fi
    fi
    rm -f /run/custom-distro/pam-auth-qualification "$runtime/shadow.new" || result=1
    if [ "$result" != 0 ] || [ "$restored" != 1 ]; then
        rm -f /run/custom-distro/pam-authentication-ok
        echo CUSTOM_PAM_AUTHENTICATION_FAILED
        exit 1
    fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
rm -f /run/custom-distro/pam-authentication-ok
while read -r expected path; do
    [ "$(sha256sum "$path" | cut -d' ' -f1)" = "$expected" ] || fail
done < /usr/share/custom-distro/pam-test/inputs.sha256
for file in password wrong-password password.hash; do
    [ "$(stat -c %u "$credentials/$file")" = 1000 ] || fail
    [ "$(stat -c %a "$credentials/$file")" = 600 ] || fail
done
[ ! -L /etc/shadow ] || fail
[ "$(stat -c %a /etc/shadow)" = 600 ] || fail
[ "$(stat -c %u /etc/shadow)" = 0 ] || fail
[ "$(grep -c '^custom:' /etc/shadow)" = 1 ] || fail
original_entry=$(sed -n '/^custom:/p' /etc/shadow)
case "$(printf '%s\n' "$original_entry" | cut -d: -f2)" in
    '!'*|'*'*) ;;
    *) fail ;;
esac
[ "$(printf '%s\n' "$original_entry" | sha256sum | cut -d' ' -f1)" = \
  "$(cat /usr/share/custom-distro/pam-test/locked-custom-entry.sha256)" ] || fail
original_sha=$(sha256sum /etc/shadow | cut -d' ' -f1)
[ ! -e "$backup" ] || fail
cp -p /etc/shadow "$backup"
chmod 600 "$backup"
backup_created=1
printf 'disposable-qemu-qualification\n' > /run/custom-distro/pam-auth-qualification
chmod 644 /run/custom-distro/pam-auth-qualification
run_probe() {
    unit=$1
    expectation=$2
    input=$3
    marker=$4
    log="$runtime/$unit.log"
    rm -f "$log"
    systemd-run --quiet --wait --collect --unit="$unit" \
        --property=User=custom --property=Group=custom --property=Type=oneshot \
        --property=NoNewPrivileges=no --property=UMask=0077 \
        --property="StandardInput=file:$credentials/$input" \
        --property="StandardOutput=file:$log" --property=StandardError=journal+console \
        "$probe" "$expectation" custom || {
            if [ -f "$log" ]; then cat "$log"; fi
            return 1
        }
    cat "$log"
    grep -qx "$marker" "$log"
}
run_probe custom-distro-pam-locked --expect-deny password CUSTOM_PAM_PASSWORD_REJECT_OK || fail
echo CUSTOM_PAM_LOCKED_DEFAULT_REJECTED
hash=$(cat "$credentials/password.hash")
case "$hash" in '$6$rounds=100000$'*) ;; *) fail ;; esac
# Use shell builtins so the enrolled hash never appears in process arguments.
while IFS= read -r entry || [ -n "$entry" ]; do
    case "$entry" in
        custom:*) printf 'custom:%s:%s\n' "$hash" "${entry#custom:*:}" ;;
        *) printf '%s\n' "$entry" ;;
    esac
done < "$backup" > "$runtime/shadow.new"
hash=
[ "$(grep -c '^custom:' "$runtime/shadow.new")" = 1 ] || fail
chmod 600 "$runtime/shadow.new"
cp "$runtime/shadow.new" /etc/shadow
chmod 600 /etc/shadow
run_probe custom-distro-pam-wrong --expect-deny wrong-password CUSTOM_PAM_PASSWORD_REJECT_OK || fail
echo CUSTOM_PAM_WRONG_PASSWORD_REJECTED
run_probe custom-distro-pam-correct --expect-success password CUSTOM_PAM_PASSWORD_LOGIN_OK || fail
cp -p "$backup" /etc/shadow
chmod 600 /etc/shadow
[ "$(sha256sum /etc/shadow | cut -d' ' -f1)" = "$original_sha" ] || fail
restored=1
rm -f "$backup" "$runtime/shadow.new" /run/custom-distro/pam-auth-qualification
cleanup
printf 'all-three-cases-passed-and-locked-shadow-restored\n' > /run/custom-distro/pam-authentication-ok || fail
echo CUSTOM_PAM_AUTHENTICATION_OK
'''


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(toolkit: PacmanToolkit) -> Path:
    project = toolkit.prefix.parents[2]
    if toolkit.prefix != project / "out/native-toolkit/prefix":
        raise ToolkitError("PAM qualification requires the private project toolkit")
    return project


def _locked_custom_entry(shadow: str) -> str:
    entries = [line for line in shadow.splitlines() if line.startswith("custom:")]
    if len(entries) != 1:
        raise ToolkitError("PAM qualification requires exactly one initial custom shadow entry")
    fields = entries[0].split(":")
    if len(fields) != 9 or not fields[1].startswith(("!", "*")):
        raise ToolkitError("PAM qualification requires the initially locked custom account")
    return entries[0] + "\n"


def prepare_pam_fixture(directory: Path, toolkit: PacmanToolkit) -> dict:
    """Export a private fixture package bound to the compiled probe and secrets' hashes."""
    project = _project(toolkit)
    directory = Path(directory).resolve()
    if not directory.is_relative_to(project / "out"):
        raise ToolkitError("PAM fixture artifacts must stay under private project output")
    current_path = project / "out/qualification/pam-auth-probe/current.json"
    if not current_path.is_file():
        raise ToolkitError("Build the target PAM qualifier with tools/build-pam-auth-probe.py --make-fixture first")
    current = json.loads(current_path.read_text())
    binary, report_path = Path(current["binary"]).resolve(), Path(current["report"]).resolve()
    if any(not path.is_relative_to(project / "out/qualification/pam-auth-probe") for path in (binary, report_path)):
        raise ToolkitError("PAM qualifier inputs point outside their private qualification output")
    report = json.loads(report_path.read_text())
    if _sha(binary) != current["sha256"] or current["sha256"] != report["sha256"] or current["identity"] != report["identity"]:
        raise ToolkitError("PAM qualifier binary/report identity changed")
    if not report["conversation_self_test_passed"] or not report["host_authentication_refused"]:
        raise ToolkitError("PAM qualifier has not passed its prerequisite tests")
    source = project / "tools/pam-auth-probe.c"
    builder = project / "tools/build-pam-auth-probe.py"
    if _sha(source) != report["inputs"]["source_sha256"] or _sha(builder) != report["inputs"]["builder_sha256"]:
        raise ToolkitError("PAM qualifier source/builder changed; rebuild it explicitly")
    prepared_credentials = current.get("disposable_fixture")
    if not prepared_credentials:
        raise ToolkitError("Prepare an explicit private password fixture with --make-fixture")
    credentials = Path(prepared_credentials["directory"]).resolve()
    if not credentials.is_relative_to(binary.parent):
        raise ToolkitError("PAM credential fixture points outside its private qualifier output")
    credential_files = {}
    for name in ("password", "wrong-password", "password.hash"):
        file = credentials / name
        if file.is_symlink() or not file.is_file() or file.stat().st_mode & 0o777 != 0o600:
            raise ToolkitError("PAM credential fixture file is not private")
        credential_files[name] = _sha(file)
    password = (credentials / "password").read_text()
    wrong = (credentials / "wrong-password").read_text()
    if not all(re.fullmatch(r"[A-Za-z0-9_-]{16,128}\n", value) for value in (password, wrong)) or password == wrong:
        raise ToolkitError("PAM fixture passwords are not distinct explicit random inputs")
    if not re.fullmatch(r"\$6\$rounds=100000\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}\n",
                        (credentials / "password.hash").read_text()):
        raise ToolkitError("PAM fixture does not contain the own-target SHA-512 hash")
    sdk = binary.parent / "sysroot"
    runtime_files = {entry["path"]: entry["sha256"] for entry in report["loaded_libraries"]}
    for path in ("usr/sbin/unix_chkpwd", "usr/lib/security/pam_unix.so"):
        runtime_files[path] = _sha(sdk / path)
    inputs = {"probe_sha256": _sha(binary), "probe_report_sha256": _sha(report_path),
              "qualifier_source_sha256": _sha(source), "builder_sha256": _sha(builder),
              "fixture_module_sha256": _sha(Path(__file__)), "credentials_sha256": credential_files,
              "check_script_sha256": hashlib.sha256(CHECK_SCRIPT.encode()).hexdigest(),
              "unit_sha256": hashlib.sha256(UNIT_TEXT.encode()).hexdigest(),
              "runtime_files_sha256": runtime_files, "sdk_packages": report["inputs"]["sdk_packages"]}
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    version = "1.0." + identity[:20]
    manifest = directory / "fixture.json"
    if manifest.exists():
        prior = json.loads(manifest.read_text())
        if prior["fixture_identity"] != identity:
            raise ToolkitError("PAM fixture inputs changed; use a fresh candidate directory")
        archive = Path(prior["initial_package"])
        if _sha(archive) != prior["sha256"]:
            raise ToolkitError("PAM fixture archive digest changed")
        inspect_package(archive)
        return prior
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    stage = directory / "stage"
    if stage.exists():
        raise ToolkitError("PAM fixture stage already exists without a completed receipt")
    for path, value in ((CHECK, CHECK_SCRIPT), ("usr/lib/systemd/system/" + UNIT, UNIT_TEXT),
                        (LOCKED_SCOPE, "unbound\n")):
        destination = stage / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value)
        destination.chmod(0o755 if path == CHECK else 0o644)
    (stage / PROBE).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, stage / PROBE)
    (stage / PROBE).chmod(0o755)
    for name in credential_files:
        destination = stage / CREDENTIALS / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(credentials / name, destination)
        destination.chmod(0o600)
    for relative in ("home/custom", "home/custom/.local", "home/custom/.local/state", CREDENTIALS):
        (stage / relative).chmod(0o700)
    checksums = [(inputs["probe_sha256"], "/" + PROBE)]
    checksums += [(value, "/" + CREDENTIALS + "/" + name) for name, value in credential_files.items()]
    checksums += [(value, "/" + name) for name, value in runtime_files.items()]
    (stage / DATA / "inputs.sha256").write_text("".join(f"{sha}  {name}\n" for sha, name in checksums))
    (stage / DATA / "fixture.json").write_text(json.dumps({"development_only": True,
        "fixture_identity": identity, "inputs": inputs, "initial_account": "locked custom UID 1000",
        "restores_shadow": True}, indent=2, sort_keys=True) + "\n")
    for name in ("inputs.sha256", "fixture.json"):
        (stage / DATA / name).chmod(0o644)
    enabled = stage / "etc/systemd/system/multi-user.target.wants" / UNIT
    enabled.parent.mkdir(parents=True, exist_ok=True)
    enabled.symlink_to("../../../../usr/lib/systemd/system/" + UNIT)
    gate = stage / "etc/systemd/system/custom-distro-system-check.service.d/pam-auth.conf"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text("[Unit]\nRequires=" + UNIT + "\nAfter=" + UNIT +
                    "\n\n[Service]\nExecStartPre=/bin/sh -ec 'test -s /run/custom-distro/pam-authentication-ok'\n")
    metadata = {"name": NAME, "version": version, "revision": 1, "arch": "x86_64",
        "description": "Private disposable guest real PAM password authentication fixture",
        "licenses": ["MIT"], "depends": ["systemd", "pam", "libxcrypt", "busybox"]}
    artifact = export_package(stage, directory / "packages", metadata,
        source_date_epoch=1756684800, provenance={"kind": "private-guest-pam-authentication-test", "fixture_identity": identity, "inputs": inputs})
    artifact.path.chmod(0o600)
    record = {"schema": 1, "development_only": True, "name": NAME,
        "fixture_identity": identity, "initial_version": version + "-1",
        "initial_package": str(artifact.path), "sha256": artifact.sha256,
        "guest_check": "/" + CHECK, "unit": UNIT, "success_marker": SUCCESS,
        "failure_marker": "CUSTOM_PAM_AUTHENTICATION_FAILED", "inputs": inputs,
        "requires_staging": True, "credentials_guest_directory": "/" + CREDENTIALS,
        "image_ownership": {"home/custom": [1000, 1000]}}
    manifest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    manifest.chmod(0o600)
    return record


def stage_pam_fixture(root: Path, fixture: dict, toolkit: PacmanToolkit) -> dict:
    """Bind a package-installed disposable root's locked initial state; never enroll."""
    project = _project(toolkit)
    root = Path(root).resolve()
    if not root.is_relative_to(project / "out/roots"):
        raise ToolkitError("PAM qualification requires a generated private image root")
    archive = Path(fixture["initial_package"])
    if _sha(archive) != fixture["sha256"]:
        raise ToolkitError("PAM qualification package differs from its prepared receipt")
    inspect_package(archive)
    installed = toolkit.query(root, fixture["name"]).splitlines()
    if f"{fixture['name']} {fixture['initial_version']}" not in installed:
        raise ToolkitError("PAM qualification package must be installed through ALPM first")
    for name, package in fixture["inputs"]["sdk_packages"].items():
        if f"{name} {package['version']}" not in toolkit.query(root, name).splitlines():
            raise ToolkitError("PAM qualification SDK differs from the installed target version")
    for path, expected in fixture["inputs"]["runtime_files_sha256"].items():
        if _sha(root / path) != expected:
            raise ToolkitError("PAM qualification target runtime bytes differ from its SDK")
    if _sha(root / PROBE) != fixture["inputs"]["probe_sha256"]:
        raise ToolkitError("Installed PAM qualifier differs from its prepared binary")
    for name, expected in fixture["inputs"]["credentials_sha256"].items():
        path = root / CREDENTIALS / name
        if path.is_symlink() or _sha(path) != expected or path.stat().st_mode & 0o777 != 0o600:
            raise ToolkitError("Installed PAM credentials differ from the private prepared fixture")
    entry = _locked_custom_entry((root / "etc/shadow").read_text())
    binding = hashlib.sha256(entry.encode()).hexdigest()
    scope = root / LOCKED_SCOPE
    if scope.is_symlink() or scope.read_text() not in {"unbound\n", binding + "\n"}:
        raise ToolkitError("PAM qualification locked scope already differs")
    scope.write_text(binding + "\n")
    scope.chmod(0o644)
    return {"fixture": fixture, "guest_check": fixture["guest_check"], "unit": fixture["unit"],
        "success_marker": SUCCESS, "initial_custom_shadow_entry_sha256": binding,
        "initial_account_locked": True, "host_authentication_performed": False,
        "account_enrolled_during_staging": False, "restore_required_before_success": True,
        "image_ownership": fixture["image_ownership"]}
