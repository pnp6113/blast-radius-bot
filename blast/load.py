"""Load /data/tests.json. Numbers are the only ids the scorer accepts."""

from __future__ import annotations

import json
import math
from pathlib import Path


def load_tests(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    tests = document["tests"]
    numbers = [test["number"] for test in tests]
    if numbers != list(range(len(tests))):
        raise ValueError(f"{path} test numbers are not 0..n-1 in order")
    return tests


def duration_ms(seconds: float) -> int:
    """score.py rounds halves up with floor(seconds * 1000 + 0.5)."""
    return math.floor(seconds * 1000 + 0.5)


def durations_ms(tests: list[dict]) -> dict[int, int]:
    return {test["number"]: duration_ms(test["seconds"]) for test in tests}
