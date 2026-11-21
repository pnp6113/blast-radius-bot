"""QA checks for diagnostics, false negatives, and sample-only trims.

These tests score the foundation selector. They do not change it. A failure
here means a measured finding moved, not that the contest entry should guess.
"""

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
from blast.load import load_tests as load_catalog  # noqa: E402
from blast.select_imports import load_map, select_imports  # noqa: E402
from tools.qa_report import expansion_report, gap_report, generalize_report, spend_report  # noqa: E402

SAMPLE = ROOT / "sample"
TESTS = load_catalog(SAMPLE / "tests.json")
ANSWERS = json.loads((SAMPLE / "answers.json").read_text(encoding="utf-8"))["commits"]
PLANS = plan_batch(SAMPLE, "imports", "auto", 4)


def _diff(path: str, body: str = "# note\n") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{body}"
        f"+{body}"
    )


class SpendTests(unittest.TestCase):
    def test_spend_matches_the_official_scorer(self) -> None:
        report = spend_report(PLANS, TESTS)
        evaluation = score.evaluate_run(answer_lines(PLANS).encode("utf-8"), TESTS, ANSWERS)
        self.assertTrue(evaluation["ok"])
        self.assertEqual(report["official_simulated_ms"], 482337)
        self.assertEqual(evaluation["simulated_milliseconds"], 482337)
        self.assertEqual(evaluation["missed_commits"], [])
        self.assertEqual(report["makespan_ms"] + report["overhead_ms"] + report["penalty_ms"], 482337)
        self.assertEqual(report["overhead_ms"], 136000)
        self.assertEqual(report["makespan_ms"], 346337)
        self.assertEqual(report["collapsed_commits"], 8)
        self.assertEqual(report["collapsed_cost_ms"], 294536)
        self.assertEqual(report["narrow_cost_ms"], 187801)

    def test_one_parser_test_sets_the_collapsed_commits(self) -> None:
        report = spend_report(PLANS, TESTS)
        longest = report["longest_critical_test"]
        parser = next(row for row in longest if row["id"].endswith("test_max_nodes"))
        self.assertEqual(parser["test_ms"], 28817)
        self.assertEqual(parser["commits"], 8)
        self.assertEqual(parser["cost_ms"], 294536)
        for row in report["commits"]:
            if row["selected"] >= 1600:
                self.assertEqual(row["workers"], 4)
                self.assertEqual(row["overhead_ms"], 8000)
                self.assertEqual(row["makespan_ms"], 28817)


class ExpansionTests(unittest.TestCase):
    def test_slowest_unfailed_tests_are_the_known_floors(self) -> None:
        report = expansion_report(PLANS, TESTS)
        slow = report["slowest_never_failed_while_selected"]
        self.assertEqual(slow[0]["id"], "tests/test_parser.py::TestParser::test_max_nodes")
        self.assertEqual(slow[0]["ms"], 28817)
        self.assertEqual(slow[0]["commits_selected"], 8)
        self.assertEqual(slow[1]["id"], "tests/test_generator.py::TestGenerator::test_generator_memory")
        self.assertEqual(slow[1]["ms"], 6686)
        self.assertEqual(slow[1]["commits_on_critical_worker"], 13)


class GeneralizeTests(unittest.TestCase):
    def test_answer_trim_does_not_survive_leave_one_commit_out(self) -> None:
        report = generalize_report(PLANS, TESTS, "auto", 4)
        self.assertEqual(report["default_simulated_ms"], 482337)
        self.assertEqual(report["oracle_failing_tests_only_ms"], 94351)
        self.assertEqual(report["oracle_headroom_ms"], 387986)
        loo = report["leave_one_commit_out"]
        self.assertEqual(loo["missed_commits"], 6)
        self.assertEqual(loo["simulated_ms"], 2101780)
        self.assertEqual(
            {row["commit"] for row in loo["misses"]},
            {"2jrq", "3utz", "6vvp", "6ze8", "ary6", "bj7g"},
        )
        kinds = {row["kind"]: row["misses"] for row in report["by_kind"]}
        self.assertEqual(kinds, {"code": 0, "multi": 0, "comment": 0, "fixture": 0})


class AdversarialSelectionTests(unittest.TestCase):
    def test_unknown_path_runs_every_test(self) -> None:
        selected, fallback, _notes = select_imports(_diff("sqlglot/not_a_real_module.py"), TESTS)
        self.assertTrue(fallback)
        self.assertEqual(len(selected), len(TESTS))

    def test_one_unknown_file_in_a_mixed_diff_still_runs_every_test(self) -> None:
        diff = _diff("sqlglot/dialects/clickhouse.py") + _diff("sqlglot/not_a_real_module.py")
        selected, fallback, _notes = select_imports(diff, TESTS)
        self.assertTrue(fallback)
        self.assertEqual(len(selected), len(TESTS))

    def test_comment_only_dialect_edit_keeps_the_mapped_tests(self) -> None:
        path = "sqlglot/dialects/clickhouse.py"
        selected, fallback, _notes = select_imports(_diff(path, "# a comment\n"), TESTS)
        mapped = load_map()["files"][path]
        self.assertFalse(fallback)
        self.assertEqual(selected, sorted(mapped))
        self.assertLess(len(selected), 200)

    def test_fixture_constant_stays_narrow_and_names_the_fixture(self) -> None:
        path = "tests/fixtures/optimizer/pushdown_predicates.sql"
        selected, fallback, _notes = select_imports(_diff(path), TESTS)
        by_number = {test["number"]: test["id"] for test in TESTS}
        self.assertFalse(fallback)
        self.assertEqual(selected, load_map()["files"][path])
        self.assertTrue(any("pushdown_predicates" in by_number[number] for number in selected))
        self.assertLessEqual(len(selected), 5)

    def test_dialects_loop_is_attached_to_a_narrow_dialect(self) -> None:
        number = next(
            test["number"]
            for test in TESTS
            if test["id"] == "tests/test_generator.py::TestGenerator::test_generator_memory"
        )
        mapped = set(load_map()["files"]["sqlglot/dialects/clickhouse.py"])
        self.assertIn(number, mapped)

    def test_typing_package_walk_is_a_false_negative(self) -> None:
        gaps = {row["id"]: row for row in gap_report(TESTS)["catalog_gaps"]}
        typing = gaps["typing-pkgutil-walk"]
        self.assertFalse(typing["selected_by_current_map"])
        self.assertEqual(typing["test_number"], 1619)

    def test_lazy_snowflake_attribute_is_a_false_negative(self) -> None:
        gaps = {row["id"]: row for row in gap_report(TESTS)["catalog_gaps"]}
        snowflake = gaps["lazy-dialect-attribute"]
        self.assertFalse(snowflake["selected_by_current_map"])
        self.assertEqual(snowflake["test_number"], 1053)
        self.assertLess(snowflake["mapped_tests_on_path"], 500)

    def test_athena_selection_is_core_fanout_not_a_precise_edge(self) -> None:
        gaps = {row["id"]: row for row in gap_report(TESTS)["catalog_gaps"]}
        athena = gaps["lazy-dialect-class-import"]
        self.assertTrue(athena["selected_by_current_map"])
        self.assertGreaterEqual(athena["mapped_tests_on_path"], 1600)


if __name__ == "__main__":
    unittest.main()
