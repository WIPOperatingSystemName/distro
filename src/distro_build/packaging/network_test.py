"""Real target curl TLS acceptance/rejection on a private loopback test server."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import subprocess
import threading
from types import SimpleNamespace

from ..graph import plan
from ..model import catalog
from ..runtime_native import RuntimeBuild
from .toolkit import PacmanToolkit


def qualify_curl(project: Path, directory: Path) -> dict:
    """Install exact candidate packages and test TLS with target libraries.

    Private disposable TLS keys remain under out/network-tests and never enter
    a package/image. The host Python TLS server is a declared test fixture; the
    consumer, trust bundle and cryptographic client libraries are target-built.
    """
    project = Path(project).resolve()
    directory = Path(directory).resolve()
    if not directory.is_relative_to(project / "out/network-tests") or directory.exists():
        raise ValueError("Use a fresh target directory under out/network-tests")
    directory.mkdir(parents=True, mode=0o700)
    artifacts, identities = [], {}
    for recipe in plan(catalog(project), ["curl"]):
        state = json.loads((project / "out/state/packages" / f"{recipe.name}.json").read_text())
        package = Path(state["path"])
        actual = hashlib.sha256(package.read_bytes()).hexdigest()
        if actual != state["sha256"]:
            raise ValueError(f"Target curl candidate changed: {package}")
        artifacts.append(package)
        identities[recipe.name] = {"path": str(package), "sha256": actual}
    root = directory / "root"
    toolkit = PacmanToolkit(project / "out/native-toolkit/prefix")
    toolkit.install(root, artifacts, bootstrap=True,
                    expected_hashes={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts})
    empty_config = directory / "openssl.cnf"
    empty_config.write_text("")
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C",
                   "OPENSSL_CONF": str(empty_config), "OPENSSL_MODULES": str(root / "usr/lib/ossl-modules")}
    context = SimpleNamespace(stage=root, sysroot=root, work=directory, env=environment)
    loader = lambda path: RuntimeBuild.closed_loader(context, path)
    key, certificate = directory / "private-test-key.pem", directory / "test-server.pem"
    subprocess.run([*loader(root / "usr/bin/openssl"), "req", "-x509", "-newkey", "rsa:2048",
                    "-sha256", "-noenc", "-days", "1", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost", "-keyout", str(key), "-out", str(certificate)],
                   env=environment, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    payload = b"CUSTOM_CURL_TLS_OK\n"

    class Response(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                # The deliberately rejected TLS clients can disconnect before
                # this fixture writes its response. Positive output is checked.
                pass

        def log_message(self, *arguments: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Response)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(certificate), str(key))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = [*loader(root / "usr/bin/curl"), "--silent", "--show-error", "--fail",
               "--noproxy", "*", "--max-time", "10"]
    port = server.server_address[1]
    try:
        positive = subprocess.run([*command, "--cacert", str(certificate), f"https://localhost:{port}/"],
                                  env=environment, capture_output=True, check=True)
        if positive.stdout != payload:
            raise RuntimeError("Target curl TLS response differs")
        untrusted = subprocess.run([*command, "--cacert", str(root / "etc/ssl/certs/ca-certificates.crt"),
                                   f"https://localhost:{port}/"], env=environment, capture_output=True)
        hostname = subprocess.run([*command, "--cacert", str(certificate), f"https://127.0.0.1:{port}/"],
                                  env=environment, capture_output=True)
        if untrusted.returncode != 60 or hostname.returncode != 60:
            raise RuntimeError(f"Target curl failed to reject TLS peer: untrusted={untrusted.returncode}, hostname={hostname.returncode}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result = {"schema": 1, "target_client": True, "private_host_fixture_server": True,
              "trusted_certificate_accepted": True, "untrusted_certificate_rejected": True,
              "wrong_hostname_rejected": True, "artifacts": identities,
              "test_private_key_bundled": False, "success_marker": "CUSTOM_CURL_TLS_OK"}
    (directory / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    print("CUSTOM_CURL_TLS_OK", flush=True)
    return result
