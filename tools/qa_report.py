#!/usr/bin/env python3
"""QA measurements for the Blast Radius selector. This file does not select tests.

It reads the public plan_batch / assign / map interfaces and the sample answers,
then writes a reproducible report. Sample answers are an evaluation oracle.
Nothing here is safe to copy into the selector.

    python tools/qa_report.py spend
    python tools/qa_report.py expansion
    python tools/qa_report.py generalize
    python tools/qa_report.py gaps
    python tools/qa_report.py bench
    python tools/qa_report.py all

`all` skips Docker. `bench` repeats the in-process plan and, unless
--no-docker is set, runs the contest image twice.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import score  # noqa: E402

from blast.engine import CommitPlan, answer_lines, plan_batch  # noqa: E402
from blast.load import duration_ms, durations_ms, load_tests  # noqa: E402
from blast.patch import changed_files, read_patches  # noqa: E402
from blast.schedule import assign  # noqa: E402
from blast.select_imports import load_map, select_imports  # noqa: E402

SAMPLE = ROOT / "sample"
SOURCE = ROOT / ".cache" / "sqlglot"
OUT = ROOT / "experiments" / "qa"
ANSWERS = json.loads((SAMPLE / "answers.json").read_text(encoding="utf-8"))["commits"]


def main() -> int:
    parser = argparse.ArgumentParser(description="QA reports. Sample numbers are not the final score.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("spend", "expansion", "generalize", "gaps", "all"):
        command = sub.add_parser(name)
        _add_strategy_args(command)
    bench = sub.add_parser("bench")
    _add_strategy_args(bench)
    bench.add_argument("--repeat", type=int, default=5)
    bench.add_argument("--no-docker", action="store_true")
    args = parser.parse_args()
    tests = load_tests(SAMPLE / "tests.json")
    plans, _cpu = _plans(args.strategy, args.scheduler, args.workers)
    if args.command == "spend":
        payload = spend_report(plans, tests)
    elif args.command == "expansion":
        payload = expansion_report(plans, tests)
    elif args.command == "generalize":
        payload = generalize_report(plans, tests, args.scheduler, args.workers)
    elif args.command == "gaps":
        payload = gap_report(tests)
    elif args.command == "bench":
        payload = bench_report(args.strategy, args.scheduler, args.workers, args.repeat, not args.no_docker)
    else:
        payload = {
            "spend": spend_report(plans, tests),
            "expansion": expansion_report(plans, tests),
            "generalize": generalize_report(plans, tests, args.scheduler, args.workers),
            "gaps": gap_report(tests),
        }
    _write(args.command if args.command != "all" else "all", payload)
    _print_summary(args.command, payload)
    return 0


def _add_strategy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", default="imports")
    parser.add_argument("--scheduler", default="auto")
    parser.add_argument("--workers", type=int, default=4)


def _plans(strategy: str, scheduler: str, workers: int) -> tuple[list[CommitPlan], float]:
    started = time.process_time()
    plans = plan_batch(SAMPLE, strategy, scheduler, workers)
    return plans, time.process_time() - started


def _write(name: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {path}", file=sys.stderr)


def spend_report(plans: list[CommitPlan], tests: list[dict]) -> dict:
    """Where each commit's simulated milliseconds go, checked against score.py."""
    by_number = {test["number"]: test for test in tests}
    durations = durations_ms(tests)
    diffs = dict(read_patches(SAMPLE / "commits"))
    evaluation = score.evaluate_run(answer_lines(plans).encode("utf-8"), tests, ANSWERS)
    if not evaluation["ok"]:
        raise SystemExit(f"selector output rejected: {evaluation['code']}")

    commits = []
    makespan_total = 0
    overhead_total = 0
    for plan in plans:
        failing = set(ANSWERS[plan.commit]["failing"])
        selected = [number for worker in plan.workers for number in worker]
        occupied = [worker for worker in plan.workers if worker]
        loads = [sum(durations[number] for number in worker) for worker in occupied]
        makespan = max(loads) if loads else 0
        overhead = score.WORKER_COST_MILLISECONDS * len(occupied)
        missed = any(number not in set(selected) for number in failing)
        penalty = score.MISS_PENALTY_MILLISECONDS if missed else 0
        critical = occupied[loads.index(makespan)] if occupied else []
        ranked = sorted(critical, key=lambda number: durations[number], reverse=True)
        critical_rows = [_test_row(number, by_number, durations, failing) for number in ranked[:5]]
        slack = sum(makespan - load for load in loads)
        failing_on_critical = sum(durations[number] for number in critical if number in failing)
        commits.append(
            {
                "commit": plan.commit,
                "kind": ANSWERS[plan.commit].get("kind", ""),
                "changed": changed_files(diffs[plan.commit]),
                "selected": len(selected),
                "failing": len(failing),
                "workers": len(occupied),
                "worker_loads_ms": loads,
                "makespan_ms": makespan,
                "overhead_ms": overhead,
                "slack_ms": slack,
                "penalty_ms": penalty,
                "cost_ms": makespan + overhead + penalty,
                "fallback": plan.fallback,
                "comment_only": plan.comment_only,
                "critical_tests": critical_rows,
                "failing_ms_on_critical_worker": failing_on_critical,
            }
        )
        makespan_total += makespan
        overhead_total += overhead

    cost_total = sum(row["cost_ms"] for row in commits)
    if cost_total != evaluation["simulated_milliseconds"]:
        raise SystemExit(f"spend {cost_total} != official {evaluation['simulated_milliseconds']}")

    collapsed = [row for row in commits if row["selected"] >= 1600]
    narrow = [row for row in commits if row["selected"] < 1600]
    longest: dict[str, dict] = {}
    for row in commits:
        if not row["critical_tests"]:
            continue
        top = row["critical_tests"][0]
        bucket = longest.setdefault(
            top["id"],
            {"id": top["id"], "test_ms": top["ms"], "commits": 0, "makespan_ms": 0, "cost_ms": 0},
        )
        bucket["commits"] += 1
        bucket["makespan_ms"] += row["makespan_ms"]
        bucket["cost_ms"] += row["cost_ms"]
    return {
        "warning": (
            "Sample-set breakdown of the current selector. The final batch is unseen. "
            "A test that did not fail here can still fail there."
        ),
        "official_simulated_ms": evaluation["simulated_milliseconds"],
        "misses": evaluation["missed_commits"],
        "makespan_ms": makespan_total,
        "overhead_ms": overhead_total,
        "penalty_ms": sum(row["penalty_ms"] for row in commits),
        "collapsed_commits": len(collapsed),
        "collapsed_cost_ms": sum(row["cost_ms"] for row in collapsed),
        "narrow_commits": len(narrow),
        "narrow_cost_ms": sum(row["cost_ms"] for row in narrow),
        "longest_critical_test": sorted(longest.values(), key=lambda row: row["cost_ms"], reverse=True),
        "commits": sorted(commits, key=lambda row: row["cost_ms"], reverse=True),
    }


def expansion_report(plans: list[CommitPlan], tests: list[dict]) -> dict:
    """Slow selected tests, and map entries that are wide relative to observed failures."""
    by_number = {test["number"]: test for test in tests}
    durations = durations_ms(tests)
    document = load_map() or {}
    files = document.get("files", {})
    diffs = dict(read_patches(SAMPLE / "commits"))

    charged: dict[int, dict] = {}
    commit_rows = []
    for plan in plans:
        failing = set(ANSWERS[plan.commit]["failing"])
        changed = changed_files(diffs[plan.commit])
        selected = [number for worker in plan.workers for number in worker]
        occupied = [worker for worker in plan.workers if worker]
        loads = [sum(durations[number] for number in worker) for worker in occupied]
        makespan = max(loads) if loads else 0
        critical = set(occupied[loads.index(makespan)]) if occupied else set()
        for number in selected:
            row = charged.setdefault(
                number,
                {
                    "number": number,
                    "id": by_number[number]["id"],
                    "file": by_number[number]["file"],
                    "ms": durations[number],
                    "commits_selected": 0,
                    "commits_on_critical_worker": 0,
                    "commits_failed": 0,
                },
            )
            row["commits_selected"] += 1
            row["commits_on_critical_worker"] += int(number in critical)
            row["commits_failed"] += int(number in failing)

        expansions = []
        for path in changed:
            mapped = files.get(path.replace("\\", "/"), [])
            mapped_set = set(mapped)
            extra = [number for number in mapped_set if number not in failing]
            extra_ms = sum(durations[number] for number in extra)
            critical_extra = [
                _test_row(number, by_number, durations, failing)
                for number in sorted(extra, key=lambda item: durations[item], reverse=True)[:5]
                if number in critical
            ]
            expansions.append(
                {
                    "path": path,
                    "mapped": len(mapped),
                    "mapped_ms": sum(durations[number] for number in mapped_set),
                    "observed_failing_in_map": len(mapped_set & failing),
                    "extra_tests": len(extra),
                    "extra_ms": extra_ms,
                    "slowest_extra_on_critical_worker": critical_extra,
                }
            )
        commit_rows.append(
            {
                "commit": plan.commit,
                "kind": ANSWERS[plan.commit].get("kind", ""),
                "selected": len(selected),
                "failing": len(failing),
                "makespan_ms": makespan,
                "files": expansions,
            }
        )

    never_failed = [row for row in charged.values() if row["commits_failed"] == 0 and row["commits_selected"] > 0]
    never_failed.sort(key=lambda row: (row["ms"], row["commits_on_critical_worker"]), reverse=True)
    on_critical = [row for row in charged.values() if row["commits_on_critical_worker"] > 0]
    on_critical.sort(key=lambda row: (row["commits_on_critical_worker"], row["ms"]), reverse=True)
    widest = sorted(commit_rows, key=lambda row: max((item["extra_ms"] for item in row["files"]), default=0), reverse=True)
    return {
        "warning": (
            "extra_tests are selected tests that did not fail on this sample commit. "
            "Dropping them would lower the sample score and can miss a final-batch failure."
        ),
        "slowest_never_failed_while_selected": never_failed[:15],
        "most_often_on_critical_worker": on_critical[:15],
        "widest_commits": widest[:12],
    }


def generalize_report(plans: list[CommitPlan], tests: list[dict], scheduler: str, workers: int) -> dict:
    """Check two answer-using trims. Both are illegal to ship. They measure overfitting."""
    by_number = {test["number"]: test for test in tests}
    durations = durations_ms(tests)
    by_kind: dict[str, dict] = {}
    for plan in plans:
        kind = ANSWERS[plan.commit].get("kind", "")
        bucket = by_kind.setdefault(kind, {"kind": kind, "commits": 0, "cost_ms": 0, "misses": 0})
        cost, missed = _rescore_numbers(
            [number for worker in plan.workers for number in worker],
            plan.workers,
            by_number,
            durations,
            set(ANSWERS[plan.commit]["failing"]),
            already_assigned=True,
        )
        bucket["commits"] += 1
        bucket["cost_ms"] += cost
        bucket["misses"] += int(missed)

    oracle_plans = []
    loo_misses = []
    loo_cost = 0
    unique_failures = []
    for plan in plans:
        failing = list(ANSWERS[plan.commit]["failing"])
        failing_set = set(failing)
        others: set[int] = set()
        for commit, answer in ANSWERS.items():
            if commit != plan.commit:
                others.update(answer["failing"])
        only_here = sorted(failing_set - others)
        if only_here:
            unique_failures.append(
                {
                    "commit": plan.commit,
                    "kind": ANSWERS[plan.commit].get("kind", ""),
                    "unique_failing": [
                        {"number": number, "id": by_number[number]["id"]} for number in only_here
                    ],
                }
            )
        oracle_workers = assign(failing, by_number, durations, workers, scheduler)
        oracle_plans.append(oracle_workers)
        trimmed = [number for number in plan.selected if number in others]
        trimmed_workers = assign(trimmed, by_number, durations, workers, scheduler)
        cost, missed = _rescore_numbers(
            trimmed, trimmed_workers, by_number, durations, failing_set, already_assigned=True
        )
        loo_cost += cost
        if missed:
            missing = [by_number[number]["id"] for number in failing if number not in set(trimmed)]
            loo_misses.append({"commit": plan.commit, "missing": missing})

    oracle_cost = 0
    for workers_assigned, plan in zip(oracle_plans, plans):
        failing_set = set(ANSWERS[plan.commit]["failing"])
        cost, _missed = _rescore_numbers(
            [number for worker in workers_assigned for number in worker],
            workers_assigned,
            by_number,
            durations,
            failing_set,
            already_assigned=True,
        )
        oracle_cost += cost

    default_cost = sum(
        _rescore_numbers(
            plan.selected,
            plan.workers,
            by_number,
            durations,
            set(ANSWERS[plan.commit]["failing"]),
            already_assigned=True,
        )[0]
        for plan in plans
    )
    return {
        "warning": (
            "The oracle and the leave-one-commit trim both read sample answers. "
            "Shipping either one hardcodes the sample. A lower oracle number is headroom "
            "only if a coverage proof selects those tests without using the answers."
        ),
        "default_simulated_ms": default_cost,
        "by_kind": sorted(by_kind.values(), key=lambda row: row["cost_ms"], reverse=True),
        "oracle_failing_tests_only_ms": oracle_cost,
        "oracle_headroom_ms": default_cost - oracle_cost,
        "leave_one_commit_out": {
            "rule": (
                "On each commit, drop every selected test that failed on no other sample commit, "
                "then reschedule with the same scheduler."
            ),
            "simulated_ms": loo_cost,
            "missed_commits": len(loo_misses),
            "misses": loo_misses,
        },
        "failures_unique_to_one_commit": unique_failures,
    }


def gap_report(tests: list[dict]) -> dict:
    """False-negative probes against the pinned tree. Skips cleanly when the checkout is absent."""
    by_id = {test["id"]: test["number"] for test in tests}
    document = load_map() or {}
    files = document.get("files", {})
    catalog_gaps = _catalog_gaps(by_id, files)
    if not (SOURCE / "sqlglot").is_dir():
        return {
            "source": None,
            "catalog_gaps": catalog_gaps,
            "dynamic_walks": [],
            "lazy_dialect_imports": [],
            "note": "Pinned checkout is not at .cache/sqlglot, so source scans were skipped.",
        }
    return {
        "source": "eb11aec4be8cb2122afe713a4816662c3f0fb2fa",
        "catalog_gaps": catalog_gaps,
        "dialect_fanout": _dialect_fanout(files),
        "core_dialect_bridges": _core_dialect_bridges(SOURCE, files),
        "dynamic_walks": _dynamic_walk_gaps(SOURCE, tests, files),
        "lazy_dialect_imports": _lazy_dialect_gaps(SOURCE, tests, files),
        "shared_utility": _shared_utility(files),
    }


def bench_report(strategy: str, scheduler: str, workers: int, repeat: int, use_docker: bool) -> dict:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        plans, cpu = _plans(strategy, scheduler, workers)
        samples.append(
            {
                "process_seconds": round(cpu, 6),
                "wall_seconds": round(time.perf_counter() - started, 6),
                "commits": len(plans),
            }
        )
    process = [row["process_seconds"] for row in samples]
    payload: dict = {
        "repeat": repeat,
        "process_seconds_min": min(process),
        "process_seconds_median": statistics.median(process),
        "process_seconds_max": max(process),
        "samples": samples,
        "note": (
            "process_seconds is this interpreter, not the contest container's user+system time. "
            "The board adds container CPU on top of simulated seconds."
        ),
    }
    if use_docker:
        payload["docker"] = _docker_bench()
    return payload


def _catalog_gaps(by_id: dict[str, int], files: dict) -> list[dict]:
    """Gaps that can be checked from the committed map and catalog, with no checkout."""
    probes = [
        {
            "id": "typing-pkgutil-walk",
            "test_id": "tests/test_typing.py::TestTyping::test_no_redundant_expression_metadata",
            "path": "sqlglot/typing/snowflake.py",
            "reason": (
                "The test importlib-loads every sqlglot.typing module through an f-string. "
                "The map records string constants, not that walk."
            ),
        },
        {
            "id": "lazy-dialect-class-import",
            "test_id": "tests/test_dialect_imports.py::TestDialectImports::test_dialect_import_no_deadlock",
            "path": "sqlglot/dialects/athena.py",
            "reason": (
                "The test imports Athena inside the test body. athena.py is already in the "
                "near-full import closure, so the map selects this test for a different reason. "
                "The lazy import itself is not an edge."
            ),
        },
        {
            "id": "lazy-dialect-attribute",
            "test_id": "tests/test_dialect_imports.py::TestDialectImports::test_dialect_attribute_access_is_cached",
            "path": "sqlglot/dialects/snowflake.py",
            "reason": "The test loads sqlglot.dialects.Snowflake by attribute, not by a string constant.",
        },
    ]
    rows = []
    for probe in probes:
        number = by_id.get(probe["test_id"])
        mapped = set(files.get(probe["path"], []))
        present = number is not None and number in mapped
        rows.append(
            {
                **probe,
                "test_number": number,
                "mapped_tests_on_path": len(mapped),
                "selected_by_current_map": present,
            }
        )
    return rows


def _dynamic_walk_gaps(source: Path, tests: list[dict], files: dict) -> list[dict]:
    """Test functions that import_module an f-string prefix and walk a package."""
    tests_by_file: dict[str, list[dict]] = {}
    for test in tests:
        tests_by_file.setdefault(test["file"], []).append(test)
    gaps = []
    for path in (source / "tests").rglob("*.py"):
        relative = path.relative_to(source).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in [item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            prefixes = _import_prefixes(node)
            if not prefixes or not _walks_modules(node):
                continue
            matched = _match_function(relative, _class_name(tree, node), node.name, tests_by_file.get(relative, []))
            if not matched:
                continue
            numbers = {test["number"] for test in matched}
            for prefix in prefixes:
                package = prefix.replace(".", "/")
                for module in sorted((source / package).glob("*.py")) if (source / package).is_dir() else []:
                    module_path = module.relative_to(source).as_posix()
                    if module.name == "__init__.py":
                        continue
                    missing = sorted(numbers - set(files.get(module_path, [])))
                    if missing:
                        gaps.append(
                            {
                                "test_file": relative,
                                "function": node.name,
                                "prefix": prefix,
                                "module": module_path,
                                "missing_tests": missing,
                            }
                        )
    return gaps


def _dialect_fanout(files: dict) -> list[dict]:
    rows = []
    for path, numbers in files.items():
        if not path.startswith("sqlglot/dialects/") or not path.endswith(".py"):
            continue
        if path.endswith("__init__.py"):
            continue
        rows.append({"path": path, "mapped_tests": len(numbers)})
    rows.sort(key=lambda row: row["mapped_tests"], reverse=True)
    return rows


def _core_dialect_bridges(source: Path, files: dict) -> list[dict]:
    """Direct imports that pull a dialect module into the near-full test set."""
    wide = {
        path
        for path, numbers in files.items()
        if path.startswith("sqlglot/dialects/")
        and path != "sqlglot/dialects/dialect.py"
        and not path.endswith("__init__.py")
        and len(numbers) >= 1600
    }
    bridges = []
    for path in (source / "sqlglot").rglob("*.py"):
        relative = path.relative_to(source).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("sqlglot.dialects."):
                targets.append(node.module.replace(".", "/") + ".py")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("sqlglot.dialects."):
                        targets.append(alias.name.replace(".", "/") + ".py")
            for target in targets:
                if target not in wide:
                    continue
                bridges.append(
                    {
                        "importer": relative,
                        "imports": target,
                        "importer_mapped_tests": len(files.get(relative, [])),
                        "nested_in_function": _nested_in_function(tree, node),
                    }
                )
    bridges.sort(key=lambda row: row["importer_mapped_tests"], reverse=True)
    return bridges


def _nested_in_function(tree: ast.AST, target: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and target in list(ast.walk(node)):
            return True
    return False


def _lazy_dialect_gaps(source: Path, tests: list[dict], files: dict) -> list[dict]:
    """A lazy dialect load that the map does not already select.

    Module-level ``from sqlglot.dialects import BigQuery`` fails collection of
    every test in the file. An attribute load inside one test fails that test.
    """
    tests_by_file: dict[str, list[dict]] = {}
    for test in tests:
        tests_by_file.setdefault(test["file"], []).append(test)
    by_number = {test["number"]: test["id"] for test in tests}
    gaps = []
    for path in (source / "tests").rglob("*.py"):
        relative = path.relative_to(source).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        file_tests = tests_by_file.get(relative, [])
        if not file_tests:
            continue
        module_names = _module_dialect_names(tree) - {"dialect", "dialects"}
        _record_lazy_gaps(
            gaps,
            files,
            by_number,
            relative,
            "module",
            module_names,
            [test["number"] for test in file_tests],
            source,
        )
        for function in [node for node in tree.body if isinstance(node, ast.ClassDef)]:
            for child in function.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = _dialect_names_in(child) - {"dialect", "dialects"} - module_names
                matched = _match_function(relative, function.name, child.name, file_tests)
                _record_lazy_gaps(
                    gaps,
                    files,
                    by_number,
                    relative,
                    child.name,
                    names,
                    [test["number"] for test in matched],
                    source,
                )
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = _dialect_names_in(function) - {"dialect", "dialects"} - module_names
            matched = _match_function(relative, None, function.name, file_tests)
            _record_lazy_gaps(
                gaps,
                files,
                by_number,
                relative,
                function.name,
                names,
                [test["number"] for test in matched],
                source,
            )
    return gaps


def _module_dialect_names(tree: ast.AST) -> set[str]:
    """Imports and attribute loads that run while the test module is imported."""
    names: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(statement, ast.ClassDef):
            for child in statement.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                names |= _dialect_names_in(child)
            continue
        names |= _dialect_names_in(statement)
    return names


def _dialect_names_in(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom) and child.module == "sqlglot.dialects":
            for alias in child.names:
                if alias.name != "*":
                    names.add(alias.name.lower())
        elif isinstance(child, ast.Attribute):
            owner = child.value
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "sqlglot"
                and owner.attr == "dialects"
                and child.attr[:1].isupper()
            ):
                names.add(child.attr.lower())
    return names


def _record_lazy_gaps(
    gaps: list[dict],
    files: dict,
    by_number: dict[int, str],
    test_file: str,
    scope: str,
    names: set[str],
    numbers: list[int],
    source: Path,
) -> None:
    if not numbers:
        return
    for name in sorted(names):
        module_path = f"sqlglot/dialects/{name}.py"
        if not (source / module_path).is_file():
            continue
        missing = sorted(set(numbers) - set(files.get(module_path, [])))
        if not missing:
            continue
        gaps.append(
            {
                "test_file": test_file,
                "scope": scope,
                "dialect": name,
                "module": module_path,
                "missing_tests": [{"number": number, "id": by_number[number]} for number in missing],
            }
        )


def _shared_utility(files: dict) -> dict:
    interesting = [
        "sqlglot/helper.py",
        "sqlglot/dialects/dialect.py",
        "sqlglot/parser.py",
        "sqlglot/tokens.py",
        "sqlglot/expressions/__init__.py",
        "sqlglot/optimizer/__init__.py",
        "sqlglot/optimizer/optimizer.py",
        "sqlglot/optimizer/scope.py",
    ]
    return {
        path: {"mapped_tests": len(files.get(path, []))}
        for path in interesting
    }


def _import_prefixes(function: ast.AST) -> set[str]:
    prefixes = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name != "import_module" or not node.args:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.JoinedStr) and argument.values:
            first = argument.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("sqlglot."):
                prefixes.add(first.value.rstrip("."))
    return prefixes


def _walks_modules(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and node.attr in {"iter_modules", "walk_packages"}:
            return True
    return False


def _class_name(tree: ast.AST, function: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and function in node.body:
            return node.name
    return None


def _match_function(file: str, class_name: str | None, name: str, tests: list[dict]) -> list[dict]:
    if not name.startswith("test"):
        return []
    matched = []
    for test in tests:
        parts = test["id"].split("::")
        if class_name is None:
            if len(parts) == 2 and parts[-1] == name:
                matched.append(test)
        elif len(parts) >= 3 and parts[-2] == class_name and parts[-1] == name:
            matched.append(test)
    return matched


def _docker_bench() -> dict:
    image = "blast-radius-entry"
    flags = [
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
    ]
    probe = (
        "import json,time,resource,sys; sys.path.insert(0,'/app');"
        "t=time.process_time();"
        "from blast.engine import plan_batch; from pathlib import Path;"
        "plans=plan_batch(Path('/data'),'imports','auto',4);"
        "ru=resource.getrusage(resource.RUSAGE_SELF);"
        "print(json.dumps({'process_seconds':round(time.process_time()-t,6),"
        "'ru_utime':ru.ru_utime,'ru_stime':ru.ru_stime,'maxrss_kb':ru.ru_maxrss,"
        "'commits':len(plans)}))"
    )
    cpu_run = subprocess.run(
        ["docker", "run", *flags, "--entrypoint", "python", image, "-c", probe],
        capture_output=True,
    )
    if cpu_run.returncode != 0:
        return {
            "ok": False,
            "stderr": cpu_run.stderr.decode("utf-8", errors="replace")[-2000:],
        }
    try:
        measured = json.loads(cpu_run.stdout.decode("utf-8").strip().splitlines()[-1])
    except json.JSONDecodeError:
        return {"ok": False, "stderr": cpu_run.stdout.decode("utf-8", errors="replace")[-2000:]}

    digests = []
    scores = []
    for _ in range(2):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            destination = Path(handle.name)
        run = subprocess.run(
            ["docker", "run", *flags, image],
            stdout=destination.open("wb"),
            stderr=subprocess.PIPE,
        )
        stdout = destination.read_bytes()
        destination.unlink(missing_ok=True)
        if run.returncode != 0:
            return {
                "ok": False,
                "cpu": measured,
                "stderr": run.stderr.decode("utf-8", errors="replace")[-2000:],
            }
        digests.append(hashlib.sha256(stdout).hexdigest())
        evaluation = score.evaluate_run(stdout, load_tests(SAMPLE / "tests.json"), ANSWERS)
        scores.append(
            {
                "ok": evaluation["ok"],
                "simulated_ms": evaluation.get("simulated_milliseconds"),
                "misses": evaluation.get("missed_commits"),
            }
        )
    return {
        "ok": True,
        "cpu": measured,
        "stdout_sha256": digests,
        "stdout_identical": digests[0] == digests[1],
        "scores": scores,
        "scores_identical": scores[0] == scores[1],
    }


def _rescore_numbers(
    selected: list[int],
    workers: list[list[int]],
    by_number: dict[int, dict],
    durations: dict[int, int],
    failing: set[int],
    already_assigned: bool,
) -> tuple[int, bool]:
    chosen = workers if already_assigned else assign(selected, by_number, durations, 4, "auto")
    occupied = [worker for worker in chosen if worker]
    present = {number for worker in occupied for number in worker}
    missed = any(number not in present for number in failing)
    cost = 0
    if occupied:
        loads = [sum(durations[number] for number in worker) for worker in occupied]
        cost = max(loads) + score.WORKER_COST_MILLISECONDS * len(occupied)
    if missed:
        cost += score.MISS_PENALTY_MILLISECONDS
    return cost, missed


def _test_row(number: int, by_number: dict[int, dict], durations: dict[int, int], failing: set[int]) -> dict:
    test = by_number[number]
    return {
        "number": number,
        "id": test["id"],
        "file": test["file"],
        "ms": durations[number],
        "failed_here": number in failing,
    }


def _print_summary(command: str, payload: dict) -> None:
    if command == "all":
        _print_summary("spend", payload["spend"])
        _print_summary("generalize", payload["generalize"])
        _print_summary("gaps", payload["gaps"])
        return
    if command == "spend":
        print(
            f"simulated {payload['official_simulated_ms']} ms  "
            f"makespan {payload['makespan_ms']}  overhead {payload['overhead_ms']}  "
            f"penalty {payload['penalty_ms']}"
        )
        print(
            f"collapsed commits (>1600 tests): {payload['collapsed_commits']} "
            f"cost {payload['collapsed_cost_ms']} ms"
        )
        print(
            f"narrow commits: {payload['narrow_commits']} cost {payload['narrow_cost_ms']} ms"
        )
        print(f"{'commit':6} {'kind':8} {'sel':5} {'w':2} {'span':7} {'over':6} {'cost':7} critical")
        for row in payload["commits"]:
            top = row["critical_tests"][0]["id"].split("::")[-1] if row["critical_tests"] else "-"
            print(
                f"{row['commit']:6} {row['kind']:8} {row['selected']:5} {row['workers']:2} "
                f"{row['makespan_ms']:7} {row['overhead_ms']:6} {row['cost_ms']:7} {top}"
            )
        return
    if command == "expansion":
        print("slowest selected tests that failed on no sample commit:")
        for row in payload["slowest_never_failed_while_selected"][:8]:
            print(
                f"  {row['ms']:6} ms  selected {row['commits_selected']:2}  "
                f"critical {row['commits_on_critical_worker']:2}  {row['id']}"
            )
        return
    if command == "generalize":
        loo = payload["leave_one_commit_out"]
        print(f"default {payload['default_simulated_ms']} ms")
        print(f"oracle failing-tests-only {payload['oracle_failing_tests_only_ms']} ms")
        print(f"headroom {payload['oracle_headroom_ms']} ms (not a shippable cut)")
        print(f"leave-one-commit trim {loo['simulated_ms']} ms  misses {loo['missed_commits']}")
        for row in payload["by_kind"]:
            print(f"  kind {row['kind']:8} commits {row['commits']:2} cost {row['cost_ms']} misses {row['misses']}")
        return
    if command == "gaps":
        for row in payload["catalog_gaps"]:
            state = "SELECTED" if row["selected_by_current_map"] else "MISSED"
            print(f"{state:8} {row['id']} -> {row['path']}")
        print(f"dynamic walk gaps: {len(payload.get('dynamic_walks', []))}")
        print(f"lazy dialect gaps: {len(payload.get('lazy_dialect_imports', []))}")
        return
    if command == "bench":
        print(
            f"in-process median {payload['process_seconds_median']} s "
            f"over {payload['repeat']} runs"
        )
        docker = payload.get("docker")
        if docker:
            print(json.dumps(docker, indent=2))


if __name__ == "__main__":
    sys.exit(main())
