#!/usr/bin/env python3
"""Adversarial checks for Ethan's static hybrid and the P2 drop rule.

This does not modify his selector. It imports scripts/eval_baselines.py from
BLAST_ETHAN_ROOT (default: the research tree next to this repo) and asks
select(..., 'stmt+import') about synthetic patches his mutant sets did not
include. A lower bound is computed from the pinned tree with generic rules:
static imports, module-level lazy dialect imports, package walks, and tests
that assert a changed message. Test numbers are looked up after the rule
matches. They are not the rule.

    python tools/validate_ethan.py

Writes experiments/ethan/validation.json. Exit 0. Misses are in the JSON.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ETHAN = Path(os.environ.get("BLAST_ETHAN_ROOT", r"D:\Hackathon\ethan-blast\blast-radius-research"))
SRC = Path(os.environ.get("BLAST_SQLGLOT", ROOT / ".cache" / "sqlglot"))
KIT = ETHAN / "kit"
ART = ETHAN / "artifacts"


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    eb = _load_ethan()
    tests = eb.tests
    by_id = {test["id"]: test for test in tests}
    by_number = {test["number"]: test for test in tests}
    graph = ImportGraph(SRC)
    print("loaded selector", flush=True)
    cases = []
    cases.append(_syntax_case(eb, graph, by_number, "sqlglot/typing/snowflake.py", _body_line(SRC / "sqlglot/typing/snowflake.py")))
    cases.append(_syntax_case(eb, graph, by_number, "sqlglot/dialects/snowflake.py", _body_line(SRC / "sqlglot/dialects/snowflake.py")))
    cases.append(_syntax_case(eb, graph, by_number, "sqlglot/dialects/bigquery.py", _body_line(SRC / "sqlglot/dialects/bigquery.py")))
    cases.append(_syntax_case(eb, graph, by_number, "sqlglot/generator.py", 5744))
    cases.append(_syntax_case(eb, graph, by_number, "sqlglot/optimizer/pushdown_predicates.py", 36))
    cases.append(_message_case(eb, by_id, by_number))
    cases.append(_structural(eb, "new-python-file", _new_file_diff()))
    cases.append(_deleted_case(eb, graph, by_number))
    cases.append(_collection_proof())
    cases.append(_structural(eb, "edited-test-source", _replace(SRC, "tests/test_parser.py", 1, "import gc  # adversarial edit")))
    cases.append(_fixture_case(eb))
    cases.append(_p2_drop(eb, by_id, by_number))
    report = {
        "ethan_root": str(ETHAN),
        "sqlglot": str(SRC),
        "selector": "eval_baselines.select mode=stmt+import",
        "warning": (
            "Sample answers are not used. A miss here is a patch this selector has not seen. "
            "One missed failing test on the final batch costs 300 seconds."
        ),
        "cases": cases,
        "miss_count": sum(1 for case in cases if case.get("misses")),
    }
    destination = ROOT / "experiments" / "ethan" / "validation.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {destination}")
    print(f"cases {len(cases)} with misses {report['miss_count']}")
    for case in cases:
        missing = case.get("misses") or []
        status = "MISS" if missing else ("ERROR" if case.get("error") else "ok")
        print(f"{status:5} {case['name']:28} selected {case.get('selected', '-')} missing {len(missing)} {case.get('error', '')}")
        for item in missing[:4]:
            print(f"       {item}")
    return 0


def _load_ethan():
    sys.argv = ["eval_baselines.py", str(KIT), str(SRC), str(ART)]
    sys.path.insert(0, str(ETHAN / "scripts"))
    import eval_baselines as eb  # noqa: WPS433

    return eb


def _body_line(path: Path) -> int:
    """A line inside a function. Import-time analysis ignores function bodies, so this stays fast."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            return node.body[0].lineno
    return 1


def _budget_case() -> dict:
    """A module-level syntax error exercises import-time name following. Bound the wait."""
    import subprocess

    code = (
        "import os,sys\n"
        f"sys.path.insert(0, {str(ETHAN / 'scripts')!r})\n"
        f"sys.argv=['eval_baselines.py', {str(KIT)!r}, {str(SRC)!r}, {str(ART)!r}]\n"
        "import eval_baselines as eb\n"
        "from pathlib import Path\n"
        f"p=Path({str(SRC)!r})/'sqlglot/dialects/snowflake.py'\n"
        "line=p.read_text(encoding='utf-8').splitlines()[0]\n"
        "n=1\n"
        "diff=('diff --git a/sqlglot/dialects/snowflake.py b/sqlglot/dialects/snowflake.py\\n'"
        "'--- a/sqlglot/dialects/snowflake.py\\n+++ b/sqlglot/dialects/snowflake.py\\n'"
        "f'@@ -{n},1 +{n},1 @@\\n-{line}\\n+def !!!\\n')\n"
        "sel,notes=eb.select(diff,'stmt+import')\n"
        "print(len(sel))\n"
    )
    try:
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return {
            "name": "budget:module-level-syntax-snowflake",
            "error": "timed out after 45s",
            "selected": None,
            "misses": ["import-time analysis did not finish within 45s"],
        }
    if completed.returncode != 0:
        return {
            "name": "budget:module-level-syntax-snowflake",
            "error": completed.stderr[-500:],
            "selected": None,
            "misses": ["selector raised"],
        }
    return {
        "name": "budget:module-level-syntax-snowflake",
        "selected": int(completed.stdout.strip().splitlines()[-1]),
        "error": None,
        "misses": [],
        "note": "Finished. The per-line import closure cases are the miss list.",
    }


def _replace(src: Path, path: str, lineno: int, new_text: str) -> str:
    old = (src / path).read_text(encoding="utf-8").splitlines()[lineno - 1]
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -{lineno},1 +{lineno},1 @@\n"
        f"-{old}\n"
        f"+{new_text}\n"
    )


def _new_file_diff() -> str:
    path = "sqlglot/dialects/adversarial_unseen.py"
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        "+X = 1\n"
    )


def _deleted_diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-x\n"
    )


def _run_select(eb, diff: str) -> dict:
    try:
        selected, notes = eb.select(diff, "stmt+import")
    except Exception as error:
        return {"selected": None, "notes": [], "error": f"{type(error).__name__}: {error}"}
    return {"selected": sorted(selected), "notes": _notes(notes), "error": None}


def _notes(notes) -> list:
    out = []
    for note in notes[:8]:
        out.append(note if isinstance(note, str) else list(note) if not isinstance(note, tuple) else [str(part)[:80] for part in note])
    return out


def _syntax_case(eb, graph, by_number, path: str, lineno: int) -> dict:
    diff = _replace(SRC, path, lineno, "def !!!")
    print(f"select {path}:{lineno}", flush=True)
    result = _run_select(eb, diff)
    print(f"  selected {0 if result['selected'] is None else len(result['selected'])} {result['error']}", flush=True)
    required = graph.tests_broken_by_syntax(path)
    return _compare(f"syntax:{path}", result, required, by_number, {"path": path, "line": lineno, "required": len(required)})


def _message_case(eb, by_id, by_number) -> dict:
    """Changing the max-nodes error text must keep the test that asserts that text."""
    path = "sqlglot/parser.py"
    lineno = 2203
    old = (SRC / path).read_text(encoding="utf-8").splitlines()[lineno - 1]
    diff = _replace(SRC, path, lineno, old.replace("Maximum number of AST nodes", "Node ceiling"))
    print("select parser.py max-nodes message", flush=True)
    result = _run_select(eb, diff)
    required = _tests_whose_function_contains(SRC, by_id, "Maximum number of AST nodes")
    heavy = _tests_whose_function_contains_tracemalloc_and_dialects(SRC, by_id)
    return _compare(
        "message:max-nodes-text",
        result,
        required,
        by_number,
        {"path": path, "line": lineno, "required": len(required), "generator_memory_tests_not_this_patch": sorted(heavy)},
    )


def _fixture_case(eb) -> dict:
    path = "tests/fixtures/identity.sql"
    line = (SRC / path).read_text(encoding="utf-8").splitlines()[0]
    diff = _replace(SRC, path, 1, line + " -- adversarial")
    result = _run_select(eb, diff)
    readers = [item for item in eb.reads.get(path, []) if isinstance(item, int)]
    return {
        "name": "fixture:identity.sql",
        "selected": None if result["selected"] is None else len(result["selected"]),
        "error": result["error"],
        "map_readers": len(readers),
        "misses": [],
        "note": "Unseen fixture. Selection is the file-read map, checked for a crash and a non-empty map.",
        "empty_map": not readers,
        "selected_matches_map": result["selected"] == sorted(set(readers)) if result["selected"] is not None else False,
    }


def _deleted_case(eb, graph, by_number) -> dict:
    path = "sqlglot/dialects/clickhouse.py"
    result = _structural(eb, "deleted-python-file", _deleted_diff(path))
    required = graph.tests_broken_by_syntax(path)
    if result["error"]:
        result["misses"] = ["selector raised"]
        return result
    chosen = set()
    # Re-select to compare. _structural already ran select; repeat is deterministic and cached maps.
    again = _run_select(eb, _deleted_diff(path))
    chosen = set(again["selected"] or [])
    missing = sorted(required - chosen)
    result["required"] = len(required)
    result["misses"] = [by_number[number]["id"] for number in missing]
    return result


def _collection_proof() -> dict:
    """Import a test module against a syntax-broken dialect, the way pytest collection does."""
    import subprocess
    import tempfile

    script = r"""
import importlib.abc, importlib.util, sys
from pathlib import Path
src = Path(sys.argv[1])
sys.path.insert(0, str(src))
path = src / "sqlglot" / "dialects" / "snowflake.py"
broken = "def !!!\n" + path.read_text(encoding="utf-8")
class Overlay(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name == "sqlglot.dialects.snowflake":
            return importlib.util.spec_from_file_location(name, str(globals()["path"]), loader=self)
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        exec(compile(broken, str(path), "exec"), module.__dict__)
sys.meta_path.insert(0, Overlay())
import tests.dialects.test_dialect
"""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        script_path = handle.name
    completed = subprocess.run([sys.executable, script_path, str(SRC)], capture_output=True, text=True)
    Path(script_path).unlink(missing_ok=True)
    line = (completed.stderr or completed.stdout).strip().splitlines()
    summary = line[-1] if line else ""
    failed = completed.returncode != 0 and "SyntaxError" in (completed.stderr + completed.stdout)
    return {
        "name": "pytest-collection:snowflake-syntax",
        "selected": None,
        "error": None if failed else summary,
        "returncode": completed.returncode,
        "exception": summary[:300],
        "misses": [] if failed else ["collection did not fail"],
        "note": "tests.dialects.test_dialect imports Snowflake at module level. A syntax error fails collection before any assertion.",
    }


def _structural(eb, name: str, diff: str) -> dict:
    from blast.patchparse import parse

    parsed = []
    parse_error = None
    try:
        for item in parse(diff):
            parsed.append({"path": item.path, "status": item.status, "binary": item.binary})
    except Exception as error:
        parse_error = f"{type(error).__name__}: {error}"
    result = _run_select(eb, diff)
    return {
        "name": name,
        "patchparse": parsed,
        "patchparse_error": parse_error,
        "selected": None if result["selected"] is None else len(result["selected"]),
        "error": result["error"],
        "fell_back_to_all": result["selected"] is not None and len(result["selected"]) == len(eb.ALL),
        "misses": ["selector raised"] if result["error"] else [],
    }


def _p2_drop(eb, by_id, by_number) -> dict:
    """P2 drops tests slower than 2s when every probed cheap test passes."""
    path = "sqlglot/parser.py"
    lineno = 2203
    old = (SRC / path).read_text(encoding="utf-8").splitlines()[lineno - 1]
    diff = _replace(SRC, path, lineno, old.replace("Maximum number of AST nodes", "Node ceiling"))
    result = _run_select(eb, diff)
    if result["error"]:
        return {"name": "p2:drop-heavy-if-cheap-probes-pass", "error": result["error"], "misses": ["selector raised"]}
    required = _tests_whose_function_contains(SRC, by_id, "Maximum number of AST nodes")
    kept, how = _p2_assuming_probes_pass(eb, set(result["selected"]))
    missing = sorted(required - kept)
    return {
        "name": "p2:drop-heavy-if-cheap-probes-pass",
        "static_selected": len(result["selected"]),
        "p2_kept": len(kept),
        "how": how,
        "selected": len(kept),
        "error": None,
        "misses": [by_number[number]["id"] for number in missing],
        "required_ids": [by_number[number]["id"] for number in sorted(required)],
    }


def _p2_assuming_probes_pass(eb, cand: set[int]) -> tuple[set[int], str]:
    """Same branches as scripts/eval_policy2.py when every probe returns pass."""
    seconds = {test["number"]: test["seconds"] for test in eb.tests}
    bound = 2.0
    alpha = 1.3
    overhead = 0.15
    prior = 0.26
    cheap = sorted((number for number in cand if seconds[number] <= bound), key=lambda number: seconds[number])
    big = set(cand) - set(cheap)

    def cost(sel: set[int]) -> float:
        return eb.schedule(list(sel))[0] / 1000

    def cpu_est(numbers) -> float:
        return (alpha * sum(seconds[number] for number in numbers) + overhead) if numbers else 0.0

    if not cand:
        return set(), "empty"
    if cpu_est(cheap) > prior * cost(cand) and not big:
        return set(cand), "include-all(no probe worth it)"
    stage1 = []
    acc = 0.0
    for number in cheap:
        if stage1 and acc + seconds[number] > 0.3:
            break
        stage1.append(number)
        acc += seconds[number]
    rest = [number for number in cheap if number not in stage1]
    live = not cheap
    must = big if live else set()
    if live and rest:
        marginal = cost(must | set(rest)) - cost(must)
        if marginal <= cpu_est(rest):
            return must | set(rest), "live: include rest"
        return must, "live: probe rest all pass"
    if rest:
        return set(), "all pass"
    return must, "stage1 only"


def _compare(name, result, required: set[int], by_number, extra: dict) -> dict:
    if result["error"]:
        return {"name": name, "error": result["error"], "selected": None, "misses": ["selector raised"], **extra}
    chosen = set(result["selected"])
    missing = sorted(required - chosen)
    return {
        "name": name,
        "selected": len(chosen),
        "error": None,
        "notes": result["notes"],
        "misses": [by_number[number]["id"] for number in missing],
        "miss_numbers": missing[:40],
        **extra,
    }


def _function_sources(src: Path, by_id: dict) -> list[tuple[int, str, str]]:
    """(test number, function name, source) parsed once per test file."""
    by_file: dict[str, list[dict]] = {}
    for test in by_id.values():
        by_file.setdefault(test["file"], []).append(test)
    rows = []
    for file, tests in by_file.items():
        path = src / file
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        names = {test["id"].split("::")[-1]: test["number"] for test in tests}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in names:
                continue
            segment = ast.get_source_segment(text, node) or ""
            rows.append((names[node.name], node.name, segment))
    return rows


def _tests_whose_function_contains(src: Path, by_id: dict, sentence: str) -> set[int]:
    return {number for number, _name, segment in _function_sources(src, by_id) if sentence in segment}


def _tests_whose_function_contains_tracemalloc_and_dialects(src: Path, by_id: dict) -> set[int]:
    return {
        number
        for number, _name, segment in _function_sources(src, by_id)
        if "tracemalloc" in segment and "Dialects" in segment
    }


class ImportGraph:
    """Static imports plus the lazy dialect loads a syntax error actually breaks."""

    def __init__(self, src: Path) -> None:
        self.src = src
        self.edges: dict[str, set[str]] = {}
        self._build()

    def _build(self) -> None:
        files = [
            path.relative_to(self.src).as_posix()
            for path in self.src.rglob("*.py")
            if ".git" not in path.parts
        ]
        existing = set(files)
        for path in files:
            self.edges[path] = self._imports(path, existing)

    def _imports(self, path: str, existing: set[str]) -> set[str]:
        try:
            tree = ast.parse((self.src / path).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            return set()
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found |= self._files(alias.name, existing)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                if node.level:
                    module = _relative(path, module, node.level)
                found |= self._files(module, existing)
                if node.module == "sqlglot.dialects":
                    for alias in node.names:
                        if alias.name not in {"*", "Dialect", "Dialects"}:
                            found |= self._files(f"sqlglot.dialects.{alias.name.lower()}", existing)
        return found

    def _files(self, module: str, existing: set[str]) -> set[str]:
        parts = module.split(".")
        if not parts or parts[0] not in {"sqlglot", "tests"}:
            return set()
        found = set()
        for index in range(len(parts)):
            prefix = "/".join(parts[: index + 1])
            for candidate in (prefix + ".py", prefix + "/__init__.py"):
                if candidate in existing:
                    found.add(candidate)
        return found

    def tests_broken_by_syntax(self, broken: str) -> set[int]:
        """Catalog tests whose module transitively imports the broken file.

        A syntax error fails import of every module that reaches the file,
        including a test module. Dynamic package walks are added when the
        broken file sits in the package they scan.
        """
        reverse: dict[str, set[str]] = {}
        for path, deps in self.edges.items():
            for dep in deps:
                reverse.setdefault(dep, set()).add(path)
        seen = set()
        stack = [broken]
        while stack:
            current = stack.pop()
            for parent in reverse.get(current, ()):
                if parent in seen:
                    continue
                seen.add(parent)
                stack.append(parent)
        catalog = json.loads((KIT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
        numbers = {test["number"] for test in catalog if test["file"] in seen}
        numbers |= self._dynamic_walkers(broken, catalog)
        numbers |= self._attribute_loaders(broken, catalog)
        return numbers

    def _dynamic_walkers(self, broken: str, catalog: list[dict]) -> set[int]:
        if not broken.startswith("sqlglot/typing/") or broken.endswith("__init__.py"):
            return set()
        by_file: dict[str, list[dict]] = {}
        for test in catalog:
            by_file.setdefault(test["file"], []).append(test)
        numbers = set()
        for file, tests in by_file.items():
            path = self.src / file
            if not path.is_file():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            names = {test["id"].split("::")[-1]: test["number"] for test in tests}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names and _walks_typing_package(node):
                    numbers.add(names[node.name])
        return numbers

    def _attribute_loaders(self, broken: str, catalog: list[dict]) -> set[int]:
        """Tests that load sqlglot.dialects.<Class> lazily, so a syntax error fails at runtime."""
        if not broken.startswith("sqlglot/dialects/") or broken.endswith(("dialect.py", "__init__.py")):
            return set()
        stem = Path(broken).stem
        by_file: dict[str, list[dict]] = {}
        for test in catalog:
            by_file.setdefault(test["file"], []).append(test)
        numbers = set()
        for file, tests in by_file.items():
            path = self.src / file
            if not path.is_file():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            names = {test["id"].split("::")[-1]: test["number"] for test in tests}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names and _loads_dialect(node, stem):
                    numbers.add(names[node.name])
        return numbers


def _relative(current: str, module: str, level: int) -> str:
    parts = current.split("/")[:-1]
    if level > 1:
        parts = parts[: -(level - 1)]
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)


def _loads_dialect(function: ast.AST, stem: str) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.ImportFrom) and node.module == "sqlglot.dialects":
            for alias in node.names:
                if alias.name.lower() == stem:
                    return True
        elif isinstance(node, ast.Attribute):
            owner = node.value
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "sqlglot"
                and owner.attr == "dialects"
                and node.attr.lower() == stem
            ):
                return True
    return False


def _walks_typing_package(function: ast.AST) -> bool:
    walks = False
    prefix = False
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and node.attr in {"iter_modules", "walk_packages"}:
            walks = True
        if isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("sqlglot.typing"):
                prefix = True
    return walks and prefix


if __name__ == "__main__":
    sys.exit(main())
