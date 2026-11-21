"""Strategy `hybrid`: Ethan's static hybrid (statement coverage + import-time names + dialect/class filters + fixture
reads + AST-equal => nothing), integrated with conservative guards for edits that can break importing a module.

Maps (built from the pinned sqlglot commit only, never from answers) live in maps/hybrid/:
  covmap_line.json.gz      {"line_tests": {file: {line: [map test numbers]}}}  per-test coverage incl. process-pool work
  dialect_usage_map.json   {"sqlglot.dialects.<stem>.<Class>": [map test numbers]}  dialects each test instantiated
  file_reads_map.json      {repo path: [map test numbers]}  data files each test opened
  test_ids.json            {"tests": [test id by map number]}  numbering the maps were built with
The pinned sqlglot source is read from BLAST_SQLGLOT_SRC (default /opt/sqlglot, fetched at image build).
Any doubt widens the selection: unknown file kinds, new/deleted/renamed/binary files, patches that do not apply or
do not compile -> every test; import-breaking edits -> the file-level import closure (maps/dependencies.json).
"""

from __future__ import annotations

import ast
import gzip
import re
import json
import os
from pathlib import Path

from blast import patchparse
from blast.hybrid_index import Analyzer, Index
from blast.select_imports import load_map as load_import_map

ROOT = Path(__file__).resolve().parent.parent
_STATE: dict = {}


def _maps_dir() -> Path:
    return Path(os.environ.get("BLAST_HYBRID_MAPS", ROOT / "maps" / "hybrid"))


def _src_dir() -> Path:
    env = os.environ.get("BLAST_SQLGLOT_SRC")
    if env:
        return Path(env)
    for cand in (Path("/opt/sqlglot"), ROOT / ".cache" / "sqlglot"):
        if (cand / "sqlglot" / "__init__.py").is_file():
            return cand
    return Path("/opt/sqlglot")


def _load(tests: list[dict]) -> dict:
    key = id(tests)
    if _STATE.get("key") == key:
        return _STATE
    md = _maps_dir()
    with gzip.open(md / "covmap_line.json.gz", "rt", encoding="utf-8") as fh:
        cov_raw = json.load(fh)["line_tests"]
    dmap_raw = json.loads((md / "dialect_usage_map.json").read_text(encoding="utf-8"))
    reads_raw = json.loads((md / "file_reads_map.json").read_text(encoding="utf-8"))
    map_ids = json.loads((md / "test_ids.json").read_text(encoding="utf-8"))["tests"]
    # translate map numbering -> this run's numbering by test id (ids are stable; numbers might not be)
    run_num = {t["id"]: t["number"] for t in tests}
    trans = {i: run_num[tid] for i, tid in enumerate(map_ids) if tid in run_num}
    unknown = sorted(n for tid, n in run_num.items() if tid not in set(map_ids))
    identity = all(trans.get(i) == i for i in range(len(map_ids))) and len(map_ids) == len(tests)

    def tr(seq):
        if identity:
            return list(seq)
        return [trans[x] for x in seq if x in trans]

    cov = {f: {ln: tr(ts) for ln, ts in lines.items()} for f, lines in cov_raw.items()} if not identity else cov_raw
    dmap = {k: tr(v) for k, v in dmap_raw.items()}
    reads = {k: tr(x for x in v if isinstance(x, int)) for k, v in reads_raw.items()}
    src = _src_dir()
    idx_path = Path(os.environ.get("BLAST_HYBRID_INDEX", md / "index.pkl"))
    index = Index.load(idx_path) if idx_path.is_file() else Index(src)
    _STATE.clear()
    _STATE.update(key=key, cov=cov, reads=reads, src=src, analyzer=Analyzer(index, cov, dmap),
                  all=[t["number"] for t in tests], unknown=unknown, spans={}, trees={})
    return _STATE


# ---------------------------------------------------------------- source helpers
def _tree(st, path):
    if path not in st["trees"]:
        st["trees"][path] = ast.parse((st["src"] / path).read_text(encoding="utf-8"))
    return st["trees"][path]


def _stmt_spans(st, path):
    if path not in st["spans"]:
        spans = []
        for n in ast.walk(_tree(st, path)):
            if isinstance(n, ast.stmt):
                end = n.end_lineno
                body = getattr(n, "body", None)
                if isinstance(body, list) and body and isinstance(body[0], ast.stmt):
                    end = body[0].lineno - 1  # header only for compound statements
                spans.append((n.lineno, max(end, n.lineno)))
        st["spans"][path] = spans
    return st["spans"][path]


def _touched(fp) -> set:
    """Old-file lines removed, plus both neighbours of every insertion point (prototype semantics)."""
    s = set()
    for h in fp.hunks:
        o = h.old_start if h.old_len else h.old_start + 1
        for tag, _ in h.lines:
            if tag == "-":
                s.add(o)
                o += 1
            elif tag == "+":
                s.update({o - 1, o})
            else:
                o += 1
    return {x for x in s if x > 0}


def _expand_stmt(st, path, lines: set) -> set:
    out = set(lines)
    spans = _stmt_spans(st, path)
    for ln in lines:
        best = None
        for a, b in spans:
            if a <= ln <= b and (best is None or b - a < best[1] - best[0]):
                best = (a, b)
        if best:
            out.update(range(best[0], best[1] + 1))
    return out


def _import_stmt(st, path, line):
    """The module/class-level statement containing `line` (None when the line is inside a function body)."""
    enclosing = [None]

    def visit(node):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if ch.lineno <= line <= ch.end_lineno:
                    return None
                continue
            if isinstance(ch, ast.ClassDef):
                if ch.lineno <= line <= ch.end_lineno:
                    enclosing[0] = ch.name
                    return visit(ch)
                continue
            if isinstance(ch, ast.stmt) and ch.lineno <= line <= ch.end_lineno:
                return ch
        return None

    node = visit(_tree(st, path))
    return node, enclosing[0]


def _toplevel_names(tree) -> set:
    out = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in ast.walk(n):
                if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                    out.add(t.id)
    return out


def _import_sig(tree) -> list:
    return sorted(ast.dump(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))


def _closure(path: str, st) -> list | None:
    doc = load_import_map()
    if not doc or "files" not in doc or path not in doc["files"]:
        return None
    valid = set(st["all"])
    return [n for n in doc["files"][path] if n in valid]


# ---------------------------------------------------------------- selection
def select_hybrid(diff: str, tests: list[dict]) -> tuple[list[int], bool, list[str]]:
    st = _load(tests)
    every = st["all"]
    notes: list[str] = []
    try:
        fps = patchparse.parse(diff)
    except patchparse.PatchError as e:
        return every, True, [f"fallback: unparseable diff ({e})"]
    if not fps:
        return every, True, ["fallback: no files in diff"]
    sel: set = set()
    cov, an = st["cov"], st["analyzer"]
    for fp in fps:
        p = fp.path
        if fp.binary or fp.status != "M" or fp.mode_change:
            return every, True, [f"fallback: {fp.status}{' binary' if fp.binary else ''} {p}"]
        if p.startswith("sqlglot/") and p.endswith(".py"):
            base_path = st["src"] / p
            if not base_path.is_file():
                return every, True, [f"fallback: {p} not in pinned tree"]
            old = base_path.read_text(encoding="utf-8")
            try:
                new = patchparse.apply(old, fp)
            except patchparse.PatchMismatch as e:
                return every, True, [f"fallback: {p} does not apply ({e})"]
            try:
                new_tree = ast.parse(new)
                compile(new, p, "exec")
            except (SyntaxError, ValueError) as e:
                return every, True, [f"fallback: {p} does not compile ({type(e).__name__})"]
            old_tree = _tree(st, p)
            head = old.splitlines()[:2] + new.splitlines()[:2]
            cookie = any(re.search(r"coding[:=]", t) for t in head)
            if not cookie and ast.dump(old_tree) == ast.dump(new_tree):
                notes.append(f"{p}: ast-equal")
                continue
            # import-breaking guard: changed imports or removed top-level names can fail collection of every test
            # file that imports this module, including lazy `sqlglot.dialects.__getattr__` and importlib walks that
            # neither coverage nor the static import graph sees (Cursor QA findings) -> every test.
            if _import_sig(old_tree) != _import_sig(new_tree) or (_toplevel_names(old_tree) - _toplevel_names(new_tree)):
                return every, True, [f"fallback: import-level change in {p}"]
            lines = _touched(fp)
            for ln in _expand_stmt(st, p, lines):
                sel.update(cov.get(p, {}).get(str(ln), ()))
            # signature rule: a changed def header (decorators/defaults/args) affects every caller of the body
            for fn in ast.walk(old_tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.body:
                    hs = min([fn.lineno] + [d.lineno for d in fn.decorator_list])
                    he = fn.body[0].lineno - 1
                    if any(hs <= ln <= he for ln in lines):
                        for ln in range(fn.body[0].lineno, fn.end_lineno + 1):
                            sel.update(cov.get(p, {}).get(str(ln), ()))
                        notes.append(f"{p}: signature {fn.name}")
            for ln in lines:
                node, cls = _import_stmt(st, p, ln)
                if node is not None:
                    ts, info = an.analyze(p, node, enclosing_class=cls)
                    sel.update(ts)
                    notes.append(f"{p}: import-time {info['names'][:3]} -> {len(ts)}")
        elif p.startswith("tests/fixtures/"):
            if p in st["reads"]:
                sel.update(st["reads"][p])
            else:
                clo = _closure(p, st)
                if clo is None:
                    return every, True, [f"fallback: unmapped fixture {p}"]
                sel.update(clo)
                notes.append(f"{p}: unmapped fixture -> closure {len(clo)}")
        else:
            return every, True, [f"fallback: {p} is not sqlglot source or a fixture"]
    if sel and st["unknown"]:
        sel.update(st["unknown"])  # tests the maps never saw run whenever anything runs
    return sorted(sel), False, notes
