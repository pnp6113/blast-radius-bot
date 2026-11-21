"""Schema, scheduler, and sample-set checks. These do not need Docker."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import score  # noqa: E402

from blast.engine import answer_lines, plan_batch  # noqa: E402
from blast.load import duration_ms  # noqa: E402
from blast.load import load_tests as load_catalog  # noqa: E402
from blast.patch import changed_files, comment_only_heuristic  # noqa: E402
from blast.schedule import assign  # noqa: E402
from starter.selector import split as starter_split  # noqa: E402

SAMPLE = ROOT / "sample"
ANSWERS = json.loads((SAMPLE / "answers.json").read_text(encoding="utf-8"))["commits"]
TESTS = load_catalog(SAMPLE / "tests.json")


class PatchTests(unittest.TestCase):
    def test_sample_patch_paths(self) -> None:
        text = (SAMPLE / "commits" / "986c.patch").read_text(encoding="utf-8")
        self.assertEqual(
            changed_files(text),
            [
                "sqlglot/dialects/singlestore.py",
                "sqlglot/typing/snowflake.py",
                "tests/fixtures/optimizer/unnest_subqueries.sql",
            ],
        )

    def test_comment_heuristic_does_not_fire_on_a_code_edit(self) -> None:
        text = (SAMPLE / "commits" / "2vdj.patch").read_text(encoding="utf-8")
        self.assertFalse(comment_only_heuristic(text))

    def test_comment_heuristic_on_a_comment_line(self) -> None:
        diff = "diff --git a/sqlglot/a.py b/sqlglot/a.py\n@@ -1 +1,2 @@\n+        # Cheap check first\n"
        self.assertTrue(comment_only_heuristic(diff))


class ScheduleTests(unittest.TestCase):
    def test_starter_split_matches_the_preserved_function(self) -> None:
        by_number = {test["number"]: test for test in TESTS}
        durations = {test["number"]: duration_ms(test["seconds"]) for test in TESTS}
        ours = assign(
            [test["number"] for test in TESTS],
            by_number,
            durations,
            3,
            "starter",
        )
        official = [worker for worker in starter_split(TESTS, 3) if worker]
        self.assertEqual(ours, official)

    def test_lpt_covers_every_number_once(self) -> None:
        tests = [
            {"number": 0, "seconds": 10, "file": "a.py"},
            {"number": 1, "seconds": 4, "file": "a.py"},
            {"number": 2, "seconds": 3, "file": "b.py"},
            {"number": 3, "seconds": 1, "file": "b.py"},
        ]
        by_number = {test["number"]: test for test in tests}
        durations = {test["number"]: duration_ms(test["seconds"]) for test in tests}
        workers = assign([0, 1, 2, 3], by_number, durations, 2, "lpt_ms")
        flat = [number for worker in workers for number in worker]
        self.assertEqual(sorted(flat), [0, 1, 2, 3])
        self.assertLessEqual(len(workers), 2)
        self.assertEqual(workers[0][0], 0)

    def test_empty_selection_runs_nothing(self) -> None:
        self.assertEqual(assign([], {}, {}, 4, "lpt_ms"), [])

    def test_worker_count_caps_at_eight(self) -> None:
        tests = [{"number": index, "seconds": 1, "file": "a.py"} for index in range(20)]
        by_number = {test["number"]: test for test in tests}
        durations = {index: 1000 for index in range(20)}
        workers = assign(list(range(20)), by_number, durations, 30, "lpt_ms")
        self.assertLessEqual(len(workers), 8)


class SampleTests(unittest.TestCase):
    def test_default_plan_is_accepted_and_misses_nothing(self) -> None:
        plans = plan_batch(SAMPLE, "all", "starter", 3)
        evaluation = score.evaluate_run(
            answer_lines(plans).encode("utf-8"),
            TESTS,
            ANSWERS,
        )
        self.assertTrue(evaluation["ok"], evaluation)
        self.assertEqual(evaluation["missed_commits"], [])
        self.assertEqual(len(plans), 30)

    def test_imports_auto_catches_every_sample_failure(self) -> None:
        plans = plan_batch(SAMPLE, "imports", "auto", 4)
        evaluation = score.evaluate_run(
            answer_lines(plans).encode("utf-8"),
            TESTS,
            ANSWERS,
        )
        self.assertTrue(evaluation["ok"], evaluation)
        self.assertEqual(evaluation["missed_commits"], [])
        self.assertEqual(evaluation["simulated_milliseconds"], 482_337)
        self.assertTrue(all(not plan.fallback for plan in plans))

    def test_module_and_imports_obey_the_schema(self) -> None:
        for strategy in ("module", "imports"):
            plans = plan_batch(SAMPLE, strategy, "lpt_ms", 4)
            evaluation = score.evaluate_run(
                answer_lines(plans).encode("utf-8"),
                TESTS,
                ANSWERS,
            )
            self.assertTrue(evaluation["ok"], (strategy, evaluation))
            for plan in plans:
                flat = [number for worker in plan.workers for number in worker]
                self.assertEqual(len(flat), len(set(flat)))
                self.assertLessEqual(len(plan.workers), 8)

    def test_runtime_sources_do_not_read_answers(self) -> None:
        for path in (ROOT / "blast").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("answers.json", text, path.name)
        entry = (ROOT / "selector.py").read_text(encoding="utf-8")
        self.assertNotIn("answers.json", entry)


if __name__ == "__main__":
    unittest.main()
