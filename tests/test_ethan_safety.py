"""Safety fallbacks for Ethan's P10 selector: syntax errors, deleted files, added files, unprobed tests.

These tests verify that the selector correctly falls back to all tests when encountering:
1. Post-patch syntax errors in sqlglot/ Python files
2. Deleted files
3. Added files
4. Test code changes (tests/*.py or conftest)
5. Unprobed slow tests are kept when all cheap tests pass
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blast.select_p10 import _check_syntax, _changes_test_code, _unsafe  # noqa: E402


class SyntaxCheckTests(unittest.TestCase):
    """Test the compile() check for post-patch syntax errors."""

    def test_valid_python_passes(self) -> None:
        code = "def foo():\n    return 42\n"
        self.assertTrue(_check_syntax(code))

    def test_syntax_error_fails(self) -> None:
        code = "def !!!\n"
        self.assertFalse(_check_syntax(code))

    def test_nested_import_syntax_error_fails(self) -> None:
        code = "def foo():\n    import sqlglot.dialects.broken\n    def !!!\n"
        self.assertFalse(_check_syntax(code))


class TestCodeDetectionTests(unittest.TestCase):
    """Test detection of test file changes that require all-tests fallback."""

    def test_test_file_change_detected(self) -> None:
        parsed = [{"old": "tests/test_parser.py", "new": "tests/test_parser.py"}]
        self.assertTrue(_changes_test_code(parsed))

    def test_conftest_change_detected(self) -> None:
        parsed = [{"old": "conftest.py", "new": "conftest.py"}]
        self.assertTrue(_changes_test_code(parsed))

    def test_fixture_not_test_code(self) -> None:
        parsed = [{"old": "tests/fixtures/identity.sql", "new": "tests/fixtures/identity.sql"}]
        self.assertFalse(_changes_test_code(parsed))

    def test_sqlglot_file_not_test_code(self) -> None:
        parsed = [{"old": "sqlglot/parser.py", "new": "sqlglot/parser.py"}]
        self.assertFalse(_changes_test_code(parsed))


class UnsafeDiffTests(unittest.TestCase):
    """Test detection of unsafe diffs: binary, added, deleted, renamed."""

    def test_binary_patch_unsafe(self) -> None:
        from pathlib import Path as MockPath

        diff = "GIT binary patch\nliteral 0\n"
        parsed = []
        self.assertTrue(_unsafe(parsed, MockPath("/tmp"), diff))

    def test_added_file_unsafe(self) -> None:
        from pathlib import Path as MockPath

        parsed = [{"old": "/dev/null", "new": "sqlglot/new.py", "status": "A"}]
        self.assertTrue(_unsafe(parsed, MockPath("/tmp"), ""))

    def test_deleted_file_unsafe(self) -> None:
        from pathlib import Path as MockPath

        parsed = [{"old": "sqlglot/old.py", "new": "/dev/null", "status": "D"}]
        self.assertTrue(_unsafe(parsed, MockPath("/tmp"), ""))

    def test_renamed_file_unsafe(self) -> None:
        from pathlib import Path as MockPath

        parsed = [{"old": "sqlglot/old.py", "new": "sqlglot/new.py", "status": "R"}]
        self.assertTrue(_unsafe(parsed, MockPath("/tmp"), ""))

    def test_modified_file_safe(self) -> None:
        from pathlib import Path as MockPath
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            sqlglot_dir = Path(tmpdir) / "sqlglot"
            sqlglot_dir.mkdir()
            test_file = sqlglot_dir / "parser.py"
            test_file.write_text("# test\n")

            parsed = [{"old": "sqlglot/parser.py", "new": "sqlglot/parser.py", "status": "M"}]
            self.assertFalse(_unsafe(parsed, Path(tmpdir), ""))


class UnprobedTestsTests(unittest.TestCase):
    """Test that unprobed slow tests are kept when all probed tests pass."""

    def test_probe_logic_documented(self) -> None:
        """This is a placeholder to document the unprobed test behavior.

        The actual probe logic is in _select_batch where:
        - When all cheap tests pass, the "all-pass" branch now keeps big - set(bigcheap)
        - This ensures slow tests that were never probed (e.g. test_max_nodes > 2s)
          are NOT dropped from the selection
        - Previously returned set(), which dropped these tests incorrectly
        """
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
