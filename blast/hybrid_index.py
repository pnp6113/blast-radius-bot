"""Import-time change analysis for the static hybrid (port of Ethan's `importtime.py`, team-internal).

For a changed statement that runs at import (module or class body), find runtime read sites of the names it assigns,
follow import-time propagation up to depth 3, map read sites to covering tests, then narrow by the dialect usage map
(dialect files) or by class users (other class bodies).

Changes from the prototype: POSIX relative paths (the prototype silently matched nothing on Windows), the index is
cacheable with pickle so the image builds it once, and dialect subclass discovery is cached.
"""

from __future__ import annotations

import ast
import pickle
import re
from pathlib import Path

DIALECT_DIRS = ("sqlglot/dialects/", "sqlglot/parsers/", "sqlglot/generators/", "sqlglot/typing/", "sqlglot/tokenizers/")


def assigned_names(stmt) -> set:
    out = set()
    for n in ast.walk(stmt):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
    return out


class Index:
    """Name read sites, class and function spans, and dialect import edges for the pinned sqlglot tree."""

    def __init__(self, src: Path):
        src = Path(src)
        self.reads: dict = {}      # name -> [(file, line, runtime, owner_names)]
        self.funcs: dict = {}      # name -> [(file, (start, end))]
        self.classes: dict = {}    # name -> [(file, (start, end))]
        self.dialect_imports: dict = {}  # dialect stem -> set of stems it imports from sqlglot.dialects
        for p in sorted((src / "sqlglot").rglob("*.py")):
            rel = p.relative_to(src).as_posix()
            text = p.read_text(encoding="utf-8")
            tree = ast.parse(text)
            self._walk(rel, tree, runtime=False, owner=None)
            for n in ast.walk(tree):
                if isinstance(n, ast.ClassDef):
                    self.classes.setdefault(n.name, []).append((rel, (n.lineno, n.end_lineno)))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.funcs.setdefault(n.name, []).append((rel, (n.lineno, n.end_lineno)))
            if rel.startswith("sqlglot/dialects/"):
                self.dialect_imports[p.stem] = set(re.findall(r"from sqlglot\.dialects\.(\w+) import", text))

    def _walk(self, rel, node, runtime, owner):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                self._walk(rel, ch, True, None)  # decorators/defaults at import; body at runtime (prototype rule)
                continue
            own = owner
            if not runtime and isinstance(ch, ast.stmt) and not isinstance(ch, ast.ClassDef):
                own = tuple(sorted(assigned_names(ch)))
            if isinstance(ch, ast.Attribute):
                self.reads.setdefault(ch.attr, []).append((rel, ch.lineno, runtime, own))
            elif isinstance(ch, ast.Name) and isinstance(ch.ctx, ast.Load):
                self.reads.setdefault(ch.id, []).append((rel, ch.lineno, runtime, own))
            elif isinstance(ch, ast.Constant) and isinstance(ch.value, str) and ch.value.isidentifier():
                self.reads.setdefault(ch.value, []).append((rel, ch.lineno, runtime, own))
            self._walk(rel, ch, runtime, own)

    def subclass_stems(self, stem: str) -> set:
        """Dialect files that import (directly or transitively) from the given dialect file."""
        out = {stem}
        changed = True
        while changed:
            changed = False
            for s, imported in self.dialect_imports.items():
                if s not in out and imported & out:
                    out.add(s)
                    changed = True
        return out

    def save(self, path: Path) -> None:
        with open(path, "wb") as fh:
            pickle.dump(self, fh, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path: Path) -> "Index":
        with open(path, "rb") as fh:
            return pickle.load(fh)


class Analyzer:
    """Index + coverage + dialect map, answering 'which tests read what this import-time statement defines'."""

    def __init__(self, index: Index, cov: dict, dialect_map: dict):
        self.idx, self.cov, self.dmap = index, cov, dialect_map
        self._dialect_users: dict = {}

    def tests_at(self, rel, line) -> set:
        return set(self.cov.get(rel, {}).get(str(line), ()))

    def dialect_users(self, stem: str) -> set:
        if stem not in self._dialect_users:
            s = set()
            for k, v in self.dmap.items():
                if k.startswith(f"sqlglot.dialects.{stem}."):
                    s.update(v)
            for d in DIALECT_DIRS:
                for _, ts in self.cov.get(f"{d}{stem}.py", {}).items():
                    s.update(ts)
            self._dialect_users[stem] = s
        return self._dialect_users[stem]

    def class_users(self, cname: str) -> set:
        idx, s = self.idx, set()
        for f, l, runtime, _own in idx.reads.get(cname, []):
            if runtime:
                s |= self.tests_at(f, l)
        for f, l, _runtime, _own in idx.reads.get(cname.lower() + "_sql", []):
            s |= self.tests_at(f, l)
        for f, (a, b) in idx.classes.get(cname, []):
            for line in range(a, b + 1):
                s |= self.tests_at(f, line)
        for f, (a, b) in idx.funcs.get(cname.lower() + "_sql", []):
            for line in range(a, b + 1):
                s |= self.tests_at(f, line)
        return s

    def analyze(self, rel: str, stmt_node, enclosing_class=None, max_depth: int = 3):
        names = set(assigned_names(stmt_node))
        tests, seen, frontier, depth = set(), set(), set(names), 0
        while frontier and depth < max_depth:
            nxt = set()
            for nm in frontier - seen:
                seen.add(nm)
                for f, l, runtime, own in self.idx.reads.get(nm, []):
                    if runtime:
                        tests |= self.tests_at(f, l)
                    elif own:
                        nxt |= set(own)
            frontier = nxt
            depth += 1
        info = {"names": sorted(names)}
        if rel.startswith(DIALECT_DIRS):
            stem = Path(rel).stem
            users = set()
            for s in self.idx.subclass_stems(stem):
                users |= self.dialect_users(s)
            info["dialect_filter"] = len(users)
            if not rel.startswith("sqlglot/dialects/dialect.py"):
                tests &= users
        elif enclosing_class:
            cu = self.class_users(enclosing_class)
            info["class_filter"] = (enclosing_class, len(cu))
            if cu:
                tests &= cu
        return tests, info
