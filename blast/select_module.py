"""Strategy B: tests whose path matches a changed module.

Uncertain changes select every test. A comment-only diff is not treated as
safe. This strategy does not know the import graph; strategy C does.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from blast.patch import changed_files


def select_module(diff: str, tests: list[dict]) -> tuple[list[int], bool, list[str]]:
    """Return (test numbers, used_fallback, notes)."""
    paths = changed_files(diff)
    notes = [f"changed {path}" for path in paths]
    if not paths:
        notes.append("no paths in diff; running every test")
        return _all(tests), True, notes

    by_file: dict[str, list[int]] = {}
    for test in tests:
        by_file.setdefault(test["file"], []).append(test["number"])

    selected: set[int] = set()
    for path in paths:
        hits, certain = _tests_for_path(path, tests, by_file)
        if not certain:
            notes.append(f"fallback: uncertain path {path}")
            return _all(tests), True, notes
        selected.update(hits)
        notes.append(f"{path} -> {len(hits)} tests")
    if not selected:
        notes.append("fallback: name match selected nothing")
        return _all(tests), True, notes
    return sorted(selected), False, notes


def _all(tests: list[dict]) -> list[int]:
    return [test["number"] for test in tests]


def _tests_for_path(
    path: str, tests: list[dict], by_file: dict[str, list[int]]
) -> tuple[set[int], bool]:
    posix = path.replace("\\", "/")
    if posix.endswith("__init__.py"):
        return set(), False
    if posix.startswith("tests/") and posix.endswith(".py"):
        hits = set(by_file.get(posix, []))
        return hits, bool(hits)
    if posix.startswith("tests/fixtures/") and posix.endswith(".sql"):
        stem = PurePosixPath(posix).stem
        hits = {test["number"] for test in tests if _id_has_stem(test["id"], stem)}
        return hits, bool(hits)
    if posix.startswith("sqlglot/") and posix.endswith(".py"):
        stem = PurePosixPath(posix).stem
        if len(stem) < 3 or stem in {"base", "core", "utils", "helper", "helpers"}:
            return set(), False
        hits = set()
        for test in tests:
            file_stem = PurePosixPath(test["file"]).stem
            if file_stem == f"test_{stem}" or _id_has_stem(test["id"], stem):
                hits.add(test["number"])
        return hits, bool(hits)
    return set(), False


def _id_has_stem(test_id: str, stem: str) -> bool:
    """Match the stem as a path or test-name token, not as a substring of another word."""
    token = stem.lower()
    for part in test_id.replace("\\", "/").lower().replace("::", "/").split("/"):
        piece = part
        if piece.startswith("test_"):
            piece = piece[len("test_") :]
        if piece == token:
            return True
    return False
