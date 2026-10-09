"""Stage the pinned native Python build tool only inside the bootstrap prefix."""
from pathlib import Path
import os
import shutil
import subprocess

source = Path(os.environ["CD_SOURCE_DIR"])
prefix = Path(os.environ["CD_TOOLS"]) / "native"
library = prefix / "lib/meson"
if library.exists():
    shutil.rmtree(library)
library.mkdir(parents=True)
shutil.copytree(source / "mesonbuild", library / "mesonbuild",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.copy2(source / "COPYING", library / "COPYING")
wrapper = prefix / "bin/meson"
wrapper.parent.mkdir(parents=True, exist_ok=True)
wrapper.write_text("#!/usr/bin/python3\nimport os, sys\n"
                   "sys.dont_write_bytecode = True\n"
                   "os.environ['PYTHONDONTWRITEBYTECODE'] = '1'\n"
                   f"sys.path.insert(0, {str(library)!r})\n"
                   "from mesonbuild.mesonmain import main\n"
                   "raise SystemExit(main())\n")
wrapper.chmod(0o755)
subprocess.run([str(wrapper), "--version"], check=True)
