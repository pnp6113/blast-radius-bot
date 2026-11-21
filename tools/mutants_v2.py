#!/usr/bin/env python3
"""LOCAL VALIDATION ONLY. Broad synthetic single-edit mutants of the pinned sqlglot, aimed at what the static
hybrid might not see. Never ship results as a patch->tests table (contest rule).

Categories (round-robin): uncovered in-function line, covered in-function line, module/class-level statement,
def default argument, string literal inside a function, statement deletion.
    python tools/mutants_v2.py SQLGLOT_SRC COVMAP_GZ OUT_DIR N SEED
"""
from __future__ import annotations

import ast
import gzip
import json
import random
import re
import sys
from pathlib import Path

OPS = [(r" == ", " != "), (r" != ", " == "), (r" < ", " <= "), (r" > ", " >= "), (r" and ", " or "), (r" or ", " and "),
       (r"\bnot ", ""), (r"\bTrue\b", "False"), (r"\bFalse\b", "True"), (r"\b(\d+)\b", None), (r" \+ ", " - "),
       (r"\[0\]", "[1]"), (r"\[-1\]", "[0]"), (r"\bNone\b", "0"), (r" is not ", " is ")]


def mutate(line, rng):
    opts = [(p, r) for p, r in OPS if re.search(p, line)]
    rng.shuffle(opts)
    for p, r in opts:
        m = rng.choice(list(re.finditer(p, line)))
        rep = str(int(m.group(1)) + 1) if r is None else r
        new = line[:m.start()] + rep + line[m.end():]
        if new != line:
            return new
    return None


def scopes(tree):
    """line -> 'func' | 'top' for statement lines; def header lines; string-literal lines inside functions."""
    kind, headers, strings = {}, set(), set()

    def walk(node, in_func):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if ch.args.defaults or ch.args.kw_defaults:
                    headers.add(ch.lineno)
                walk(ch, True)
                continue
            if isinstance(ch, ast.stmt) and not isinstance(ch, (ast.ClassDef,)) and ch.lineno == ch.end_lineno:
                kind.setdefault(ch.lineno, "func" if in_func else "top")
            if in_func and isinstance(ch, ast.Constant) and isinstance(ch.value, str) and ch.lineno == ch.end_lineno \
                    and 2 <= len(ch.value) <= 40 and ch.value.strip():
                strings.add(ch.lineno)
            walk(ch, in_func)

    walk(tree, False)
    return kind, headers, strings


def main():
    src, covp, out, n, seed = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    cov = json.load(gzip.open(covp, "rt"))["line_tests"]
    files = sorted(p.relative_to(src).as_posix() for p in (src / "sqlglot").rglob("*.py"))
    cats = (sys.argv[6].split(",") if len(sys.argv) > 6 else
            ["uncovered", "covered", "toplevel", "default", "string", "delete"])
    fixtures = sorted(p.relative_to(src).as_posix() for p in (src / "tests" / "fixtures").rglob("*.sql"))
    made, tries = 0, 0
    while made < n and tries < n * 400:
        tries += 1
        cat = cats[made % len(cats)]
        if cat == "fixture":
            f = rng.choice(fixtures)
            flines = (src / f).read_text(encoding="utf-8").split("\n")
            fpool = [i + 1 for i, x in enumerate(flines) if x.strip() and not x.startswith(("#", "--"))]
            if not fpool:
                continue
            l = rng.choice(fpool)
            old = flines[l - 1]
            new = mutate(old, rng) or (old.replace("SELECT", "SELECT DISTINCT", 1) if "SELECT" in old else None)
            if not new or new == old:
                continue
            a_, b_ = max(1, l - 2), min(len(flines), l + 2)
            hunk = [f"@@ -{a_},{b_ - a_ + 1} +{a_},{b_ - a_ + 1} @@"]
            for i in range(a_, b_ + 1):
                hunk += ["-" + old, "+" + new] if i == l else [" " + flines[i - 1]]
            patch = f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n" + "\n".join(hunk) + "\n"
            (out / f"v2_{seed}_{made:03d}_{cat}.patch").write_text(patch, encoding="utf-8")
            made += 1
            continue
        f = rng.choice(files)
        text = (src / f).read_text(encoding="utf-8")
        lines = text.split("\n")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        kind, headers, strings = scopes(tree)
        covered = set(int(x) for x in cov.get(f, {}))
        if cat == "uncovered":
            pool = [l for l, k in kind.items() if k == "func" and l not in covered]
        elif cat == "covered":
            pool = [l for l, k in kind.items() if k == "func" and l in covered]
        elif cat == "toplevel":
            pool = [l for l, k in kind.items() if k == "top" and not lines[l - 1].lstrip().startswith(("import", "from"))]
        elif cat == "default":
            pool = sorted(headers)
        elif cat == "string":
            pool = sorted(strings)
        elif cat == "insert":
            pool = [l for l, k in kind.items() if k == "func"]
        else:
            pool = [l for l, k in kind.items() if k == "func" and not lines[l - 1].strip().startswith(("return", "raise", "pass", "else", "elif", "try", "except", "finally"))]
        if not pool:
            continue
        l = rng.choice(pool)
        old = lines[l - 1]
        if cat == "insert":
            indent = old[: len(old) - len(old.lstrip())]
            new = indent + rng.choice(["return None", "raise ValueError('x')"]) + "\n" + old
        elif cat == "string":
            m = re.search(r"(['\"])([^'\"]{2,40})\1", old)
            new = old[:m.start(2)] + m.group(2)[::-1] + old[m.end(2):] if m and m.group(2)[::-1] != m.group(2) else None
        elif cat == "delete":
            indent = old[: len(old) - len(old.lstrip())]
            new = indent + "pass"
        else:
            new = mutate(old, rng)
        if not new or new == old:
            continue
        mod = lines[:]
        mod[l - 1] = new
        try:
            if ast.dump(ast.parse("\n".join(mod))) == ast.dump(tree):
                continue
        except SyntaxError:
            continue
        a, b = max(1, l - 3), min(len(lines), l + 3)
        new_lines = new.split("\n")
        hunk = [f"@@ -{a},{b - a + 1} +{a},{b - a + len(new_lines)} @@"]
        for i in range(a, b + 1):
            hunk += (["-" + old] + ["+" + x for x in new_lines]) if i == l else [" " + lines[i - 1]]
        patch = f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n" + "\n".join(hunk) + "\n"
        (out / f"v2_{seed}_{made:03d}_{cat}.patch").write_text(patch, encoding="utf-8")
        made += 1
    print("made", made, "tries", tries)


if __name__ == "__main__":
    main()
