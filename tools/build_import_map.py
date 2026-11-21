#!/usr/bin/env python3
"""Build maps/dependencies.json from the pinned sqlglot checkout.

The map records which sample-catalog tests import a file, mention a fixture
stem, or name a dialect. It does not read sample/answers.json. Dynamic
loading inside dialect.py is not treated as "every test depends on every
dialect": that edge would select every test for every dialect edit. Tests
that loop over Dialects, and tests that name a dialect, are recorded.

Usage:
    python tools/build_import_map.py
    python tools/build_import_map.py path/to/sqlglot
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
DEFAULT_SOURCE = ROOT / ".cache" / "sqlglot"


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not (source / "sqlglot").is_dir():
        print(f"sqlglot checkout not found at {source}", file=sys.stderr)
        return 1
    tests = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
    files = _build(source, tests)
    document = {
        "source_commit": SOURCE_COMMIT,
        "files": files,
    }
    destination = ROOT / "maps" / "dependencies.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    print(
        f"wrote {destination} ({destination.stat().st_size} bytes, {len(files)} files)",
        file=sys.stderr,
    )
    return 0


def _build(source: Path, tests: list[dict]) -> dict[str, list[int]]:
    python_files = [
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if ".git" not in path.parts and "build" not in path.parts
    ]
    existing = set(python_files)
    edges = {path: set() for path in python_files}
    for path in python_files:
        text = (source / path).read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        allow_strings = "importlib" in text or "_LAZY_ATTRS" in text
        _collect_imports(path, tree, existing, source, edges, allow_strings)

    closures = {path: _closure(path, edges) for path in python_files}

    dependents: dict[str, set[int]] = {}

    def add(path: str, numbers: list[int]) -> None:
        if not numbers:
            return
        dependents.setdefault(path, set()).update(numbers)

    tests_by_file: dict[str, list[dict]] = {}
    for test in tests:
        tests_by_file.setdefault(test["file"], []).append(test)

    fixture_paths = [
        path.relative_to(source).as_posix()
        for path in (source / "tests" / "fixtures").rglob("*")
        if path.is_file()
    ]
    fixture_aliases = _fixture_aliases(fixture_paths)

    dialect_names = _dialect_names(source)
    family = _dialect_family(source, dialect_names)

    for path, file_tests in tests_by_file.items():
        numbers = [test["number"] for test in file_tests]
        add(path, numbers)
        for dependency in closures.get(path, set()):
            add(dependency, numbers)
        full = source / path
        if not full.is_file():
            continue
        text = full.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            for fixture in fixture_paths:
                if Path(fixture).stem in text:
                    add(fixture, numbers)
            continue
        for function in _functions(tree):
            matched = _match_tests(path, function["class"], function["name"], file_tests)
            if not matched:
                continue
            matched_numbers = [test["number"] for test in matched]
            for literal in function["strings"]:
                for fixture in fixture_aliases.get(literal, []):
                    add(fixture, matched_numbers)
                if literal in dialect_names:
                    for module in family.get(literal, []):
                        add(module, matched_numbers)
            if function["all_dialects"]:
                for modules in family.values():
                    for module in modules:
                        add(module, matched_numbers)

    _link_fixture_dialects(source, dependents, dialect_names, family)
    return {path: sorted(numbers) for path, numbers in sorted(dependents.items())}


def _fixture_aliases(fixture_paths: list[str]) -> dict[str, list[str]]:
    """Map a string a test might write to the fixture files it names."""
    aliases: dict[str, list[str]] = {}

    def add(key: str, fixture: str) -> None:
        if len(key) < 4:
            return
        aliases.setdefault(key, []).append(fixture)

    for fixture in fixture_paths:
        relative = fixture[len("tests/fixtures/") :]
        add(relative, fixture)
        add(str(Path(relative).with_suffix("")), fixture)
        add(Path(fixture).stem, fixture)
    return aliases


def _link_fixture_dialects(
    source: Path,
    dependents: dict[str, set[int]],
    dialect_names: set[str],
    family: dict[str, list[str]],
) -> None:
    """A fixture line `# dialect: bigquery` makes its tests depend on that dialect."""
    pattern = re.compile(r"(?m)^#\s*dialect:\s*(.+)$")
    snapshot = {
        path: set(numbers)
        for path, numbers in dependents.items()
        if path.startswith("tests/fixtures/") and path.endswith(".sql")
    }
    for fixture, numbers in snapshot.items():
        text = (source / fixture).read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            for token in re.split(r"[, ]+", match.group(1).strip()):
                name = token.strip().lower()
                if name not in dialect_names:
                    continue
                for module in family.get(name, []):
                    dependents.setdefault(module, set()).update(numbers)


def _dialect_names(source: Path) -> set[str]:
    names = set()
    for path in (source / "sqlglot" / "dialects").glob("*.py"):
        if path.name in {"dialect.py", "__init__.py"}:
            continue
        names.add(path.stem)
    return names


def _dialect_family(source: Path, names: set[str]) -> dict[str, list[str]]:
    family: dict[str, list[str]] = {}
    for name in names:
        modules = []
        for folder in ("dialects", "parsers", "generators", "typing"):
            candidate = f"sqlglot/{folder}/{name}.py"
            if (source / candidate).is_file():
                modules.append(candidate)
        family[name] = modules
    return family


def _collect_imports(
    path: str,
    tree: ast.AST,
    existing: set[str],
    source: Path,
    edges: dict[str, set[str]],
    allow_strings: bool,
) -> None:
    for node in _runtime_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges[path].update(_files_for_module(alias.name, source, existing))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = _relative_module(path, module, node.level)
            if module:
                edges[path].update(_files_for_module(module, source, existing))
        elif (
            allow_strings
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("sqlglot.")
        ):
            edges[path].update(_files_for_module(node.value, source, existing))


def _runtime_nodes(tree: ast.AST) -> list[ast.AST]:
    """Walk the tree, skipping bodies that run only under TYPE_CHECKING."""
    found: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            return
        found.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def _is_type_checking(test: ast.AST) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _relative_module(current: str, module: str, level: int) -> str:
    parts = current.split("/")[:-1]
    if level > 1:
        parts = parts[: -(level - 1)]
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)


def _files_for_module(module: str, source: Path, existing: set[str]) -> set[str]:
    parts = module.split(".")
    if not parts or parts[0] not in {"sqlglot", "tests"}:
        return set()
    files: set[str] = set()
    for index in range(len(parts)):
        prefix = "/".join(parts[: index + 1])
        package = prefix + "/__init__.py"
        module_file = prefix + ".py"
        last = index == len(parts) - 1
        if last and module_file in existing:
            files.add(module_file)
        elif package in existing:
            files.add(package)
        elif last and module_file in existing:
            files.add(module_file)
    return {path for path in files if (source / path).is_file()}


def _closure(start: str, edges: dict[str, set[str]]) -> set[str]:
    seen = set()
    stack = [start]
    while stack:
        current = stack.pop()
        for dependency in edges.get(current, ()):
            if dependency in seen or dependency == current:
                continue
            seen.add(dependency)
            stack.append(dependency)
    return seen


def _functions(tree: ast.AST) -> list[dict]:
    found = []

    class Walk(ast.NodeVisitor):
        def __init__(self) -> None:
            self.class_name: str | None = None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            previous = self.class_name
            self.class_name = node.name
            class_strings = _statement_strings(node.body)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    record = _function_record(child, self.class_name)
                    record["strings"] |= class_strings
                    found.append(record)
                elif isinstance(child, ast.ClassDef):
                    self.visit_ClassDef(child)
            self.class_name = previous

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if self.class_name is None:
                found.append(_function_record(node, None))

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if self.class_name is None:
                found.append(_function_record(node, None))

    Walk().visit(tree)
    return found


def _statement_strings(body: list[ast.stmt]) -> set[str]:
    """String constants on the class itself, such as dialect = \"bigquery\"."""
    found: set[str] = set()
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(statement):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return found


def _function_record(node: ast.AST, class_name: str | None) -> dict:
    strings: set[str] = set()
    all_dialects = False
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            strings.add(child.value)
        elif isinstance(child, ast.For) and _iterates_dialects(child.iter):
            all_dialects = True
    return {
        "class": class_name,
        "name": node.name,
        "strings": strings,
        "all_dialects": all_dialects,
    }


def _iterates_dialects(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "Dialects"
    if isinstance(node, ast.Attribute):
        return node.attr == "Dialects"
    return False


def _match_tests(file: str, class_name: str | None, name: str, tests: list[dict]) -> list[dict]:
    if not name.startswith("test"):
        return []
    matched = []
    for test in tests:
        if test["file"] != file:
            continue
        parts = test["id"].split("::")
        if class_name is None:
            if len(parts) == 2 and parts[-1] == name:
                matched.append(test)
        elif len(parts) >= 3 and parts[-2] == class_name and parts[-1] == name:
            matched.append(test)
    return matched


if __name__ == "__main__":
    sys.exit(main())
