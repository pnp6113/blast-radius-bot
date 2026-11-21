"""Strategy C: tests recorded for each changed file in maps/dependencies.json.

The map is built from the pinned sqlglot tree by tools/build_import_map.py.
It is not built from the sample answers. A missing file or a missing map
selects every test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from blast.patch import changed_files

_CACHE: dict | None = None
_TRIED = False


def map_file() -> Path | None:
    override = os.environ.get("BLAST_MAP")
    if override:
        path = Path(override)
        return path if path.is_file() else None
    candidates = [
        Path(__file__).resolve().parent.parent / "maps" / "dependencies.json",
        Path("/app/maps/dependencies.json"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_map() -> dict | None:
    global _CACHE, _TRIED
    if _TRIED:
        return _CACHE
    _TRIED = True
    path = map_file()
    if path is None:
        _CACHE = None
        return None
    _CACHE = json.loads(path.read_text(encoding="utf-8"))
    return _CACHE


def select_imports(diff: str, tests: list[dict]) -> tuple[list[int], bool, list[str]]:
    document = load_map()
    notes: list[str] = []
    if not document or "files" not in document:
        notes.append("fallback: dependency map is missing")
        return [test["number"] for test in tests], True, notes

    paths = changed_files(diff)
    if not paths:
        notes.append("fallback: no paths in diff")
        return [test["number"] for test in tests], True, notes

    known = document["files"]
    valid = {test["number"] for test in tests}
    selected: set[int] = set()
    for path in paths:
        posix = path.replace("\\", "/")
        if posix not in known:
            notes.append(f"fallback: {posix} is not in the dependency map")
            return [test["number"] for test in tests], True, notes
        numbers = known[posix]
        if not numbers:
            notes.append(f"fallback: {posix} has no recorded tests")
            return [test["number"] for test in tests], True, notes
        selected.update(number for number in numbers if number in valid)
        notes.append(f"{posix} -> {len(numbers)} mapped tests")
    if not selected:
        notes.append("fallback: map produced an empty selection")
        return [test["number"] for test in tests], True, notes
    return sorted(selected), False, notes
