"""Run one strategy over a data directory and return answer lines plus notes.

stdout printing lives in selector.py. This module returns objects so the
experiment harness can score several strategies in one process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from blast.load import durations_ms, load_tests
from blast.patch import comment_only_heuristic, read_patches
from blast.schedule import assign
from blast.select_imports import select_imports
from blast.select_module import select_module
from blast.select_hybrid import select_hybrid
from blast.select_p2 import select_p2


@dataclass
class CommitPlan:
    commit: str
    workers: list[list[int]]
    selected: list[int]
    fallback: bool
    notes: list[str] = field(default_factory=list)
    comment_only: bool = False


def plan_batch(
    data_dir: Path,
    strategy: str = "all",
    scheduler: str = "starter",
    worker_count: int = 3,
) -> list[CommitPlan]:
    tests = load_tests(data_dir / "tests.json")
    by_number = {test["number"]: test for test in tests}
    durations = durations_ms(tests)
    plans: list[CommitPlan] = []
    every_test = [test["number"] for test in tests]
    for commit, diff in read_patches(data_dir / "commits"):
        try:
            selected, fallback, notes = _select(strategy, diff, tests)
            workers = assign(selected, by_number, durations, worker_count, scheduler)
        except ValueError:
            raise
        except Exception as error:
            selected = every_test
            fallback = True
            notes = [f"fallback: {type(error).__name__}: {error}"]
            workers = assign(selected, by_number, durations, min(worker_count, 4), "lpt_ms")
        plans.append(
            CommitPlan(
                commit=commit,
                workers=workers,
                selected=selected,
                fallback=fallback,
                notes=notes,
                comment_only=comment_only_heuristic(diff),
            )
        )
    return plans


def _select(strategy: str, diff: str, tests: list[dict]) -> tuple[list[int], bool, list[str]]:
    if strategy == "all":
        return [test["number"] for test in tests], False, ["every test"]
    if strategy == "module":
        return select_module(diff, tests)
    if strategy == "imports":
        return select_imports(diff, tests)
    if strategy == "hybrid":
        return select_hybrid(diff, tests)
    if strategy == "p2":
        return select_p2(diff, tests)
    raise ValueError(f"unknown strategy {strategy}")


def answer_lines(plans: list[CommitPlan]) -> str:
    """One JSON object per commit, with a trailing newline. Nothing else."""
    import json

    lines = [
        json.dumps({"commit": plan.commit, "workers": plan.workers}, separators=(",", ":"))
        for plan in plans
    ]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
