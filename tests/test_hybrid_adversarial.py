"""Adversarial checks for the static hybrid: risky edits must widen the selection, never shrink it.

Needs the pinned sqlglot source (BLAST_SQLGLOT_SRC, /opt/sqlglot, or .cache/sqlglot); skipped without it.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
ALL = len(TESTS)
NUM = {t["id"]: t["number"] for t in TESTS}


def _src() -> Path | None:
    for cand in (os.environ.get("BLAST_SQLGLOT_SRC"), "/opt/sqlglot", str(ROOT / ".cache" / "sqlglot"),
                 str(ROOT.parent / "blast-radius" / ".cache" / "sqlglot")):
        if cand and (Path(cand) / "sqlglot" / "__init__.py").is_file():
            return Path(cand)
    return None


SRC = _src()


def _diff(path: str, old_line: int, old: str, new: str) -> str:
    return (f"diff --git a/{path} b/{path}\nindex 1111111..2222222 100644\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -{old_line},1 +{old_line},1 @@\n-{old}\n+{new}\n")


def _line(path: str, needle: str) -> tuple[int, str]:
    for i, l in enumerate((SRC / path).read_text(encoding="utf-8").splitlines(), 1):
        if needle in l:
            return i, l
    raise AssertionError(f"{needle!r} not in {path}")


@unittest.skipIf(SRC is None, "pinned sqlglot source not available")
class HybridAdversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["BLAST_SQLGLOT_SRC"] = str(SRC)
        from blast.select_hybrid import select_hybrid
        cls.sel = staticmethod(select_hybrid)

    def run_sel(self, diff):
        numbers, fallback, notes = self.sel(diff, TESTS)
        self.assertTrue(all(isinstance(n, int) and 0 <= n < ALL for n in numbers))
        self.assertEqual(len(numbers), len(set(numbers)))
        return set(numbers), fallback, notes

    def test_syntax_error_selects_everything(self):
        n, l = _line("sqlglot/dialects/snowflake.py", "class Snowflake(")
        s, fb, notes = self.run_sel(_diff("sqlglot/dialects/snowflake.py", n, l, l.replace("(", "((")))
        self.assertEqual(len(s), ALL, notes)

    def test_removed_import_uses_closure(self):
        n, l = _line("sqlglot/dialects/snowflake.py", "from sqlglot.dialects.dialect import")
        s, fb, notes = self.run_sel(_diff("sqlglot/dialects/snowflake.py", n, l, "# " + l))
        self.assertGreater(len(s), 100, notes)  # module import closure, not a handful of tests

    def test_test_file_edit_selects_everything(self):
        n, l = _line("tests/test_parser.py", "def test_")
        s, fb, notes = self.run_sel(_diff("tests/test_parser.py", n, l, l + "  # edited"))
        self.assertEqual(len(s), ALL)

    def test_new_and_deleted_files_select_everything(self):
        new = ("diff --git a/sqlglot/newmod.py b/sqlglot/newmod.py\nnew file mode 100644\nindex 0000000..1111111\n"
               "--- /dev/null\n+++ b/sqlglot/newmod.py\n@@ -0,0 +1 @@\n+X = 1\n")
        self.assertEqual(len(self.run_sel(new)[0]), ALL)
        gone = ("diff --git a/sqlglot/lineage.py b/sqlglot/lineage.py\ndeleted file mode 100644\nindex 1111111..0000000\n"
                "--- a/sqlglot/lineage.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
        self.assertEqual(len(self.run_sel(gone)[0]), ALL)

    def test_malformed_or_mismatched_patch_selects_everything(self):
        self.assertEqual(len(self.run_sel("@@ nonsense @@\n")[0]), ALL)
        bad = _diff("sqlglot/parser.py", 5, "this line is not in the file", "x = 1")
        self.assertEqual(len(self.run_sel(bad)[0]), ALL)

    def test_unknown_file_kind_selects_everything(self):
        self.assertEqual(len(self.run_sel(_diff("setup.py", 1, "a", "b"))[0]), ALL)

    def test_fixture_comment_is_not_free(self):
        p = "tests/fixtures/optimizer/simplify.sql"
        n, l = _line(p, "SELECT") if "SELECT" in (SRC / p).read_text() else (1, (SRC / p).read_text().splitlines()[0])
        s, fb, notes = self.run_sel(_diff(p, n, l, l + " /* comment */"))
        self.assertGreater(len(s), 0)

    def test_typing_module_import_change_reaches_dynamic_typing_test(self):
        # Cursor QA: test_typing walks sqlglot.typing with importlib; an import-breaking edit must reach it.
        n, l = _line("sqlglot/typing/snowflake.py", "import")
        s, fb, notes = self.run_sel(_diff("sqlglot/typing/snowflake.py", n, l, "# " + l))
        self.assertIn(NUM["tests/test_typing.py::TestTyping::test_no_redundant_expression_metadata"], s, notes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
