"""Locks the adversarial findings for Ethan's selector. Does not modify it.

The full measurement is experiments/ethan/validation.json, produced by
tools/validate_ethan.py. These tests check that report and the generic rules
behind it. They skip when the pinned tree or the report is absent.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT = ROOT / "experiments" / "ethan" / "validation.json"
SRC = ROOT / ".cache" / "sqlglot"


def _cases() -> dict:
    document = json.loads(REPORT.read_text(encoding="utf-8"))
    return {case["name"]: case for case in document["cases"]}


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        if not REPORT.is_file():
            self.skipTest("run python tools/validate_ethan.py first")

    def test_syntax_and_structural_misses_are_recorded(self) -> None:
        cases = _cases()
        typing = cases["syntax:sqlglot/typing/snowflake.py"]
        self.assertIn(
            "tests/test_typing.py::TestTyping::test_no_redundant_expression_metadata",
            typing["misses"],
        )
        self.assertEqual(typing["selected"], 2)
        snowflake = cases["syntax:sqlglot/dialects/snowflake.py"]
        self.assertIn(
            "tests/test_dialect_imports.py::TestDialectImports::test_dialect_attribute_access_is_cached",
            snowflake["misses"],
        )
        self.assertGreater(snowflake["selected"], 0)
        self.assertEqual(cases["syntax:sqlglot/dialects/bigquery.py"]["selected"], 0)
        generator = cases["syntax:sqlglot/generator.py"]
        self.assertIn("tests/test_generator.py::TestGenerator::test_generator_memory", generator["misses"])
        self.assertIn("tests/test_parser.py::TestParser::test_max_nodes", generator["misses"])
        self.assertEqual(generator["selected"], 2)
        created = cases["new-python-file"]
        self.assertIn("FileNotFoundError", created["error"])
        deleted = cases["deleted-python-file"]
        self.assertEqual(deleted["selected"], 0)
        self.assertGreater(len(deleted["misses"]), 0)
        self.assertTrue(any("test_clickhouse.py" in item for item in deleted["misses"]))

    def test_p2_pass_branch_drops_the_asserting_test(self) -> None:
        case = _cases()["p2:drop-heavy-if-cheap-probes-pass"]
        self.assertEqual(case["p2_kept"], 0)
        self.assertEqual(case["how"], "all pass")
        self.assertEqual(case["misses"], ["tests/test_parser.py::TestParser::test_max_nodes"])
        message = _cases()["message:max-nodes-text"]
        self.assertEqual(message["misses"], [])
        self.assertGreater(message["selected"], 1000)

    def test_collection_really_fails_and_safe_fallbacks_hold(self) -> None:
        cases = _cases()
        proof = cases["pytest-collection:snowflake-syntax"]
        self.assertIn("SyntaxError", proof["exception"])
        self.assertEqual(cases["edited-test-source"]["selected"], 1620)
        self.assertTrue(cases["fixture:identity.sql"]["selected_matches_map"])


class RuleTests(unittest.TestCase):
    def setUp(self) -> None:
        if not (SRC / "sqlglot").is_dir():
            self.skipTest("pinned sqlglot checkout is not present")

    def test_attribute_rule_names_the_snowflake_loader(self) -> None:
        from tools.validate_ethan import ImportGraph

        graph = ImportGraph(SRC)
        catalog = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
        numbers = graph._attribute_loaders("sqlglot/dialects/snowflake.py", catalog)
        ids = {test["id"] for test in catalog if test["number"] in numbers}
        self.assertIn(
            "tests/test_dialect_imports.py::TestDialectImports::test_dialect_attribute_access_is_cached",
            ids,
        )

    def test_patchparse_sees_new_and_deleted_files(self) -> None:
        from blast.patchparse import parse
        from tools.validate_ethan import _deleted_diff, _new_file_diff

        added = parse(_new_file_diff())
        self.assertEqual(added[0].status, "A")
        self.assertTrue(added[0].path.endswith("adversarial_unseen.py"))
        removed = parse(_deleted_diff("sqlglot/dialects/clickhouse.py"))
        self.assertEqual(removed[0].status, "D")


if __name__ == "__main__":
    unittest.main()
