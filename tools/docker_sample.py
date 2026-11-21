#!/usr/bin/env python3
"""Build the image and score one run the way `make sample` does.

The Makefile calls python3 and is the contest's documented command. This
script is the same docker invocation for a host whose Python is `python`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "blast-radius-entry"
SAMPLE = ROOT / "sample"
OUTPUT = ROOT / "output.jsonl"


def main() -> int:
    build = subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "--tag", IMAGE, "."],
        cwd=ROOT,
    )
    if build.returncode != 0:
        return build.returncode
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--cpus",
        "2",
        "--memory",
        "4096m",
        "--memory-swap",
        "4096m",
        "--pids-limit",
        "256",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:65534",
        "--env",
        "HOME=/tmp",
        "--volume",
        f"{SAMPLE / 'commits'}:/data/commits:ro",
        "--volume",
        f"{SAMPLE / 'tests.json'}:/data/tests.json:ro",
        IMAGE,
    ]
    with OUTPUT.open("wb") as stdout:
        run = subprocess.run(command, stdout=stdout, stderr=subprocess.PIPE)
    sys.stderr.buffer.write(run.stderr)
    if run.returncode != 0:
        print(f"container exited {run.returncode}", file=sys.stderr)
        return run.returncode
    return subprocess.call(
        [sys.executable, str(ROOT / "score.py"), str(SAMPLE), str(SAMPLE / "answers.json"), str(OUTPUT)]
    )


if __name__ == "__main__":
    sys.exit(main())
