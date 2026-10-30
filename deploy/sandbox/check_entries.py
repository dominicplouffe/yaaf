"""Import every file the sandbox image ships, on the interpreter that runs it.

The control plane runs Python 3.12 and the suite tests on it; the sandbox runs
whatever python3 its base image carries (node:22-slim is Debian bookworm, so
3.11). A line that is legal on one and not the other — a quote reused inside an
f-string field is 3.12-only — passes every test and then kills every seat that
imports it, in production, as "its container stopped". That happened to wire.py:
nothing imported it in a sandbox until a seat first ran on yaaf's own loop.

So the image build runs this against /opt/yaaf, and CI runs it against the
Dockerfile's own COPY list in the Dockerfile's own base. Usage:

    python check_entries.py DIR               # every *.py in DIR
    python check_entries.py --dockerfile REPO # the files the Dockerfile copies
"""

import importlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# Module-level reads in sandbox_entry: an import needs them set, not real.
os.environ.setdefault("YAAF_API_BASE_URL", "http://check.invalid")
os.environ.setdefault("YAAF_API_TOKEN", "check")
os.environ.setdefault("YAAF_RUN_ID", "check")


def shipped(repo: Path) -> list[Path]:
    dockerfile = (repo / "deploy/sandbox/Dockerfile").read_text()
    return [repo / src for src in re.findall(r"^COPY (\S+\.py) /opt/yaaf/", dockerfile, re.M)]


def check(directory: Path) -> int:
    sys.path.insert(0, str(directory))
    failed = 0
    for path in sorted(directory.glob("*.py")):
        try:
            importlib.import_module(path.stem)
        except BaseException as error:  # a SystemExit at import is a failure too
            failed += 1
            print(f"FAIL {path.name}: {type(error).__name__}: {error}")
        else:
            print(f"ok   {path.name}")
    print(f"{sys.version.split()[0]}: {failed} of the sandbox's files will not load")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--dockerfile"]:
        with tempfile.TemporaryDirectory() as staging:
            for source in shipped(Path(argv[1])):
                shutil.copy(source, staging)
            return check(Path(staging))
    return check(Path(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
