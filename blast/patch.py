"""Read unified diffs. This does not decide which tests fail."""

from __future__ import annotations

from pathlib import Path


def changed_files(diff: str) -> list[str]:
    """Paths a unified diff touches, in the order they appear, without duplicates."""
    found: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[-1]
        if path.startswith("b/"):
            path = path[2:]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if path not in seen:
            seen.add(path)
            found.append(path)
    return found


def read_patches(commits_dir: Path) -> list[tuple[str, str]]:
    """(commit id, diff text) sorted by id, matching the starter's file order."""
    patches = []
    for path in sorted(commits_dir.glob("*.patch")):
        patches.append((path.stem, path.read_text(encoding="utf-8")))
    return patches


def comment_only_heuristic(diff: str) -> bool:
    """True when every added or removed line looks like a comment or blank.

    Scoring does not promise that a comment-only edit is harmless. Callers
    may report this hint. They must not skip tests because of it.
    """
    touched = False
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "diff ", "index ", "@@")):
            continue
        if line.startswith("+") or line.startswith("-"):
            touched = True
            body = line[1:].strip()
            if body and not body.startswith("#") and not body.startswith("--"):
                return False
    return touched
