#!/usr/bin/env python3
"""Compare selectors on the sample set with the official scorer.

Sample numbers are not the final score. The hidden batch is larger and
contains kinds of edits this set does not. This tool never writes answer
lines to stdout mixed with a report: human output is the report, and the
selector's stdout is passed to score.py as bytes.

    python tools/experiment.py compare
    python tools/experiment.py schema
    python tools/experiment.py score --strategy all
    python tools/experiment.py diagnose --strategy imports
    python tools/experiment.py schedule
    python tools/experiment.py profile
    python tools/experiment.py holdout --seed 7
"""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import random
import sys
import time
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import score  # noqa: E402

from blast.engine import CommitPlan, plan_batch  # noqa: E402
from blast.load import duration_ms, load_tests  # noqa: E402
from blast.patch import changed_files, read_patches  # noqa: E402

SAMPLE = ROOT / "sample"
ANSWERS = json.loads((SAMPLE / "answers.json").read_text(encoding="utf-8"))["commits"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Score selector strategies on the sample set.")
    sub = parser.add_subparsers(dest="command", required=True)

    schema = sub.add_parser("schema", help="official format check for the default starter plan")
    schema.add_argument("--strategy", default="all")
    schema.add_argument("--scheduler", default="starter")
    schema.add_argument("--workers", type=int, default=3)

    score_cmd = sub.add_parser("score", help="official simulated seconds for one strategy")
    _add_strategy_args(score_cmd)

    diagnose = sub.add_parser("diagnose", help="per-commit selection and misses")
    _add_strategy_args(diagnose)

    sub.add_parser("compare", help="score the reference strategies and write the experiment log")
    sub.add_parser("schedule", help="sweep worker counts and schedulers on run-all")

    holdout = sub.add_parser("holdout", help="score a seeded commit split without fitting")
    holdout.add_argument("--seed", type=int, default=7)
    holdout.add_argument("--fraction", type=float, default=0.3)

    sub.add_parser("profile", help="cProfile the default plan")

    args = parser.parse_args()
    if args.command == "schema":
        return _schema(args.strategy, args.scheduler, args.workers)
    if args.command == "score":
        return _print_score(args.strategy, args.scheduler, args.workers)
    if args.command == "diagnose":
        return _diagnose(args.strategy, args.scheduler, args.workers)
    if args.command == "compare":
        return _compare()
    if args.command == "schedule":
        return _schedule()
    if args.command == "holdout":
        return _holdout(args.seed, args.fraction)
    if args.command == "profile":
        return _profile()
    return 2


def _add_strategy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", default="all", choices=("all", "module", "imports"))
    parser.add_argument(
        "--scheduler",
        default="starter",
        choices=("starter", "lpt_ms", "round_robin", "pack_files", "auto"),
    )
    parser.add_argument("--workers", type=int, default=3)


def _plans(strategy: str, scheduler: str, workers: int) -> tuple[list[CommitPlan], float]:
    started = time.process_time()
    plans = plan_batch(SAMPLE, strategy, scheduler, workers)
    return plans, time.process_time() - started


def _stdout(plans: list[CommitPlan]) -> bytes:
    from blast.engine import answer_lines

    return answer_lines(plans).encode("utf-8")


def _schema(strategy: str, scheduler: str, workers: int) -> int:
    plans, _cpu = _plans(strategy, scheduler, workers)
    evaluation = score.evaluate_run(_stdout(plans), load_tests(SAMPLE / "tests.json"), ANSWERS)
    if not evaluation["ok"]:
        where = "" if evaluation["line"] is None else f" on line {evaluation['line']}"
        print(f"Rejected: {evaluation['code']}{where}")
        return 1
    print("format ok")
    print(f"commits {len(plans)} simulated {_ms(evaluation['simulated_milliseconds'])} s")
    return 0


def _print_score(strategy: str, scheduler: str, workers: int) -> int:
    row = _measure(strategy, scheduler, workers)
    _print_row(row)
    if row["misses"]:
        for commit in row["missed_commits"]:
            print(f"  missed {commit}")
    return 0


def _diagnose(strategy: str, scheduler: str, workers: int) -> int:
    plans, cpu = _plans(strategy, scheduler, workers)
    tests = load_tests(SAMPLE / "tests.json")
    by_number = {test["number"]: test for test in tests}
    diffs = dict(read_patches(SAMPLE / "commits"))
    evaluation = score.evaluate_run(_stdout(plans), tests, ANSWERS)
    if not evaluation["ok"]:
        print(f"Rejected: {evaluation['code']}")
        return 1
    print(
        f"{strategy} {scheduler} workers={workers} "
        f"simulated {_ms(evaluation['simulated_milliseconds'])} s "
        f"misses {len(evaluation['missed_commits'])} cpu {cpu:.4f} s"
    )
    print(f"{'commit':6} {'kind':8} {'sel':5} {'fall':4} {'ms':8} {'miss':4} changed")
    for plan in plans:
        cost, missed = _commit_cost(plan, by_number)
        kind = ANSWERS[plan.commit].get("kind", "")
        changed = ",".join(changed_files(diffs[plan.commit]))
        print(
            f"{plan.commit:6} {kind:8} {len(plan.selected):5} "
            f"{'yes' if plan.fallback else 'no':4} {cost:8} {'yes' if missed else 'no':4} {changed}"
        )
        if missed:
            missing = [
                by_number[number]["id"]
                for number in ANSWERS[plan.commit]["failing"]
                if number not in set(plan.selected)
            ]
            for test_id in missing:
                print(f"       missing {test_id}")
    return 0


def _compare() -> int:
    specs = [
        ("all", "starter", 3),
        ("all", "lpt_ms", 4),
        ("all", "lpt_ms", 8),
        ("module", "lpt_ms", 4),
        ("imports", "lpt_ms", 4),
        ("imports", "auto", 4),
        ("all", "auto", 4),
    ]
    rows = [_measure(*spec) for spec in specs]
    _print_table(rows)
    destination = ROOT / "experiments" / "latest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": (
            "Sample-set results only. The final batch is unseen and about 1.5x larger. "
            "local_cpu_seconds is this process, not the contest container."
        ),
        "rows": rows,
    }
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {destination}")
    return 0


def _schedule() -> int:
    rows = []
    for method in ("lpt_ms", "round_robin", "pack_files", "starter"):
        for workers in range(1, 9):
            rows.append(_measure("all", method, workers))
    _print_table(rows)
    best = min(rows, key=lambda row: row["simulated_milliseconds"])
    print(
        f"best run-all on this sample: {best['scheduler']} x{best['workers']} "
        f"{_ms(best['simulated_milliseconds'])} s, misses {best['misses']}"
    )
    return 0


def _holdout(seed: int, fraction: float) -> int:
    ids = sorted(ANSWERS)
    rng = random.Random(seed)
    shuffled = ids[:]
    rng.shuffle(shuffled)
    count = max(1, int(round(len(shuffled) * fraction)))
    holdout = set(shuffled[:count])
    print(f"holdout seed {seed} size {len(holdout)} of {len(ids)}")
    print("strategy    split    simulated_s  misses")
    for strategy, scheduler in (("all", "starter"), ("module", "lpt_ms"), ("imports", "auto")):
        plans, _cpu = _plans(strategy, scheduler, 3 if scheduler == "starter" else 4)
        for label, chosen in (("train", set(ids) - holdout), ("holdout", holdout)):
            subset = [plan for plan in plans if plan.commit in chosen]
            simulated, misses = _sum_plans(subset)
            print(f"{strategy:10} {label:8} {_ms(simulated):>12} {misses:7}")
    print("Holdout is a report split. These strategies are not fitted on either side.")
    return 0


def _profile() -> int:
    profiler = cProfile.Profile()
    profiler.enable()
    plan_batch(SAMPLE, "imports", "lpt_ms", 4)
    profiler.disable()
    stream = StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(20)
    print(stream.getvalue())
    return 0


def _measure(strategy: str, scheduler: str, workers: int) -> dict:
    plans, cpu = _plans(strategy, scheduler, workers)
    tests = load_tests(SAMPLE / "tests.json")
    evaluation = score.evaluate_run(_stdout(plans), tests, ANSWERS)
    if not evaluation["ok"]:
        return {
            "strategy": strategy,
            "scheduler": scheduler,
            "workers": workers,
            "ok": False,
            "code": evaluation["code"],
            "simulated_milliseconds": None,
            "misses": None,
            "missed_commits": [],
            "local_cpu_seconds": round(cpu, 6),
            "fallbacks": None,
        }
    breakdown, missed = _sum_plans(plans)
    if breakdown != evaluation["simulated_milliseconds"]:
        raise SystemExit(
            f"breakdown {breakdown} != official {evaluation['simulated_milliseconds']} "
            f"for {strategy} {scheduler} x{workers}"
        )
    return {
        "strategy": strategy,
        "scheduler": scheduler,
        "workers": workers,
        "ok": True,
        "simulated_milliseconds": evaluation["simulated_milliseconds"],
        "simulated_seconds": _ms(evaluation["simulated_milliseconds"]),
        "misses": len(evaluation["missed_commits"]),
        "missed_commits": evaluation["missed_commits"],
        "local_cpu_seconds": round(cpu, 6),
        "fallbacks": sum(plan.fallback for plan in plans),
    }


def _sum_plans(plans: list[CommitPlan]) -> tuple[int, int]:
    tests = load_tests(SAMPLE / "tests.json")
    by_number = {test["number"]: test for test in tests}
    total = 0
    misses = 0
    for plan in plans:
        cost, missed = _commit_cost(plan, by_number)
        total += cost
        misses += int(missed)
    return total, misses


def _commit_cost(plan: CommitPlan, by_number: dict[int, dict]) -> tuple[int, bool]:
    selected = {number for worker in plan.workers for number in worker}
    missed = any(number not in selected for number in ANSWERS[plan.commit]["failing"])
    occupied = [worker for worker in plan.workers if worker]
    cost = 0
    if occupied:
        loads = [
            sum(duration_ms(by_number[number]["seconds"]) for number in worker)
            for worker in occupied
        ]
        cost = max(loads) + score.WORKER_COST_MILLISECONDS * len(occupied)
    if missed:
        cost += score.MISS_PENALTY_MILLISECONDS
    return cost, missed


def _print_table(rows: list[dict]) -> None:
    print(f"{'strategy':8} {'scheduler':12} {'w':2} {'simulated_s':>12} {'miss':>5} {'fall':>5} {'cpu_s':>8}")
    for row in rows:
        _print_row(row)


def _print_row(row: dict) -> None:
    if not row["ok"]:
        print(f"{row['strategy']:8} {row['scheduler']:12} {row['workers']:2} REJECTED {row['code']}")
        return
    print(
        f"{row['strategy']:8} {row['scheduler']:12} {row['workers']:2} "
        f"{row['simulated_seconds']:>12} {row['misses']:5} {row['fallbacks']:5} "
        f"{row['local_cpu_seconds']:8.4f}"
    )


def _ms(milliseconds: int) -> str:
    return f"{milliseconds // 1000}.{milliseconds % 1000:03d}"


if __name__ == "__main__":
    sys.exit(main())
