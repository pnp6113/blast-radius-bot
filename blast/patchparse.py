"""Unified-diff parser and in-memory apply (interface from the team blueprint, section 10).

parse(text) -> list[FilePatch]
apply(base_text, FilePatch) -> str        raises PatchMismatch when the context does not match

Nothing here decides which tests fail. Everything that cannot be parsed or applied with certainty raises, so callers
fall back to a wider selection instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    """The diff text itself is malformed."""


class PatchMismatch(ValueError):
    """The diff does not apply to the base file."""


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list = field(default_factory=list)  # (tag, text) with tag in ' ', '-', '+'; text without newline
    old_no_newline: bool = False
    new_no_newline: bool = False

    def old_lines(self) -> list[str]:
        return [t for tag, t in self.lines if tag in (" ", "-")]

    def new_lines(self) -> list[str]:
        return [t for tag, t in self.lines if tag in (" ", "+")]

    def touched_old_lines(self) -> list[int]:
        """Old-file line numbers removed or replaced (1-based)."""
        out, n = [], self.old_start
        for tag, _ in self.lines:
            if tag == "-":
                out.append(n)
            if tag in (" ", "-"):
                n += 1
        return out

    def insert_points(self) -> list[int]:
        """Old-file line numbers after which new lines are inserted (0 = before line 1)."""
        out, n = [], self.old_start
        prev_tag = None
        for tag, _ in self.lines:
            if tag == "+" and prev_tag != "-":
                out.append(n - 1)
            if tag in (" ", "-"):
                n += 1
            prev_tag = tag if tag != "+" else prev_tag
        return out


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    status: str  # 'M' modify, 'A' add, 'D' delete, 'R' rename
    hunks: list = field(default_factory=list)
    binary: bool = False
    mode_change: bool = False

    @property
    def path(self) -> str:
        """The path tests would see after the change (old path for deletions)."""
        return self.new_path or self.old_path or ""


def _strip_prefix(p: str) -> str | None:
    p = p.strip()
    if "\t" in p:
        p = p.split("\t", 1)[0]
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    if p == "/dev/null":
        return None
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def parse(text: str) -> list[FilePatch]:
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the piece after the final newline is not a line
    files: list[FilePatch] = []
    cur: FilePatch | None = None
    hunk: Hunk | None = None
    remaining_old = remaining_new = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            parts = line.split()
            a = _strip_prefix(parts[2]) if len(parts) >= 4 else None
            b = _strip_prefix(parts[3]) if len(parts) >= 4 else None
            cur = FilePatch(old_path=a, new_path=b, status="M")
            files.append(cur)
            hunk = None
        elif cur is not None and hunk is None and line.startswith("new file mode"):
            cur.status, cur.old_path = "A", None
        elif cur is not None and hunk is None and line.startswith("deleted file mode"):
            cur.status, cur.new_path = "D", None
        elif cur is not None and hunk is None and line.startswith("rename from "):
            cur.status, cur.old_path = "R", line[len("rename from "):].strip()
        elif cur is not None and hunk is None and line.startswith("rename to "):
            cur.new_path = line[len("rename to "):].strip()
        elif cur is not None and hunk is None and (line.startswith("old mode") or line.startswith("new mode")):
            cur.mode_change = True
        elif cur is not None and (line.startswith("Binary files") or line.startswith("GIT binary patch")):
            cur.binary = True
        elif line.startswith("--- ") and (hunk is None or (remaining_old <= 0 and remaining_new <= 0)):
            if cur is None:  # plain unified diff without a git header
                cur = FilePatch(old_path=None, new_path=None, status="M")
                files.append(cur)
            p = _strip_prefix(line[4:])
            if p is None:
                cur.status, cur.old_path = "A", None
            elif cur.old_path is None and cur.status != "A":
                cur.old_path = p
            hunk = None
        elif line.startswith("+++ ") and cur is not None and (hunk is None or (remaining_old <= 0 and remaining_new <= 0)):
            p = _strip_prefix(line[4:])
            if p is None:
                cur.status, cur.new_path = "D", None
            elif cur.new_path is None and cur.status != "D":
                cur.new_path = p
            hunk = None
        elif line.startswith("@@"):
            m = HUNK_RE.match(line)
            if not m or cur is None:
                raise PatchError(f"bad hunk header: {line[:60]!r}")
            hunk = Hunk(int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1))
            remaining_old, remaining_new = hunk.old_len, hunk.new_len
            cur.hunks.append(hunk)
        elif hunk is not None and line.startswith("\\"):
            # "\ No newline at end of file" applies to the previous line
            if hunk.lines:
                tag = hunk.lines[-1][0]
                if tag in (" ", "-"):
                    hunk.old_no_newline = True
                if tag in (" ", "+"):
                    hunk.new_no_newline = True
        elif hunk is not None and (remaining_old > 0 or remaining_new > 0):
            tag = line[:1] if line else " "
            body = line[1:] if line else ""
            if tag not in (" ", "-", "+"):
                raise PatchError(f"unexpected line in hunk: {line[:60]!r}")
            hunk.lines.append((tag, body))
            if tag in (" ", "-"):
                remaining_old -= 1
            if tag in (" ", "+"):
                remaining_new -= 1
        # anything else (index lines, similarity, trailing text) is ignored
        i += 1
    for f in files:
        for h in f.hunks:
            if len(h.old_lines()) != h.old_len or len(h.new_lines()) != h.new_len:
                raise PatchError(f"hunk length mismatch in {f.path}")
    return files


def _find(base: list[str], seq: list[str], hint: int) -> int:
    """Index where seq occurs in base: exact at hint, else the unique occurrence nearest to hint."""
    n = len(seq)
    if n == 0:
        return max(0, min(hint, len(base)))
    if 0 <= hint <= len(base) - n and base[hint:hint + n] == seq:
        return hint
    hits = [i for i in range(len(base) - n + 1) if base[i:i + n] == seq]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise PatchMismatch("context not found")
    raise PatchMismatch("context is ambiguous")


def apply(base_text: str | None, fp: FilePatch) -> str | None:
    """Return the new file text (None for a deletion)."""
    if fp.binary:
        raise PatchMismatch("binary patch")
    if fp.status == "D":
        return None
    if fp.status == "A" or base_text is None:
        if fp.status != "A" and base_text is None:
            raise PatchMismatch("base file missing")
        base: list[str] = []
        had_final_newline = True
    else:
        crlf = "\r\n" in base_text
        text = base_text.replace("\r\n", "\n")
        had_final_newline = text.endswith("\n")
        base = text.split("\n")
        if had_final_newline:
            base = base[:-1]
    out: list[str] = []
    pos = 0
    final_newline = had_final_newline
    for h in fp.hunks:
        old = h.old_lines()
        hint = (h.old_start - 1) if h.old_len else h.old_start
        at = _find(base, old, max(0, hint))
        if at < pos:
            raise PatchMismatch("overlapping hunks")
        out.extend(base[pos:at])
        out.extend(h.new_lines())
        pos = at + len(old)
        if pos >= len(base):
            if h.new_no_newline:
                final_newline = False
            elif h.old_no_newline:
                final_newline = True
    out.extend(base[pos:])
    result = "\n".join(out) + ("\n" if final_newline and out else "")
    if fp.status != "A" and base_text is not None and "\r\n" in base_text:
        result = result.replace("\n", "\r\n")
    return result
