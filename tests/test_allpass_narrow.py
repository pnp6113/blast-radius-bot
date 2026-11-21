"""Narrow all-pass rule and package-walk edge added on top of 2f1a860."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blast.select_p10 import _changed_tokens  # noqa: E402


def _item(minus, plus):
    return {"new": "sqlglot/parser.py", "old": "sqlglot/parser.py",
            "hunks": [{"lines": [(" ", "ctx"), ("-", minus), ("+", plus)]}]}


class ChangedTokensTests(unittest.TestCase):
    def test_message_edit_yields_old_literal(self) -> None:
        toks = _changed_tokens([_item(
            '                self.raise_error(f"Maximum number of AST nodes ({self.max_nodes}) exceeded")',
            '                self.raise_error(f"Max AST node count ({self.max_nodes}) exceeded")')])
        self.assertIn("Maximum number of AST nodes", toks)

    def test_operator_only_edit_yields_nothing(self) -> None:
        toks = _changed_tokens([_item(
            "            if self.normalization_strategy is NormalizationStrategy.UPPERCASE",
            "            if self.normalization_strategy == NormalizationStrategy.UPPERCASE")])
        self.assertEqual(toks, set())

    def test_renamed_identifier_is_reported(self) -> None:
        toks = _changed_tokens([_item("    x = build_thing()", "    x = build_other()")])
        self.assertEqual(toks, {"build_thing", "build_other"})


if __name__ == "__main__":
    unittest.main()
