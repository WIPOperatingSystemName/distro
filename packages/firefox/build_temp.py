"""Private Firefox scratch space short enough for Python multiprocessing sockets."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


@contextmanager
def temporary_environment(environment: dict, output: Path):
    root = output / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fx-", dir=root) as directory:
        # multiprocessing adds /pymp-XXXXXXXX/listener-XXXXXXXX. Linux sun_path
        # has 108 bytes, including its terminator; count filesystem bytes.
        if len(os.fsencode(directory)) + len("/pymp-XXXXXXXX/listener-XXXXXXXX") > 107:
            raise RuntimeError("Firefox's temporary socket path is too long; use a shorter checkout path")
        yield {**environment, "TMPDIR": directory}
