"""Strategy `p10`: Ethan's measured policy (static hybrid + lambda-body rule + warm-parent probe).

On the 30 sample commits his harness scored 103.851 s simulated, 0 misses, about 13 s of process CPU.
Heavy tests stay out of the probe unless a cheap test already failed. A dict-values-only edit skips the
import probe: that edit cannot fail collection, and the import probe was pure CPU on top of the candidate set.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

from blast.engine import CommitPlan
from blast.load import durations_ms, load_tests
from blast.patch import comment_only_heuristic, read_patches
from blast.schedule import assign

ROOT = Path(__file__).resolve().parent.parent
CODE = Path(os.environ.get("BLAST_P10_CODE", ROOT / "research" / "ethan-p10" / "code"))
B = float(os.environ.get("BLAST_P2_B", "2.0"))
OVH = float(os.environ.get("BLAST_P10_OVH", "0.01"))
P_PASS = {
    "code": float(os.environ.get("BLAST_P2_PPASS", "0.26")),
    "fixture": float(os.environ.get("BLAST_P2_PPASS_FIX", "0.02")),
}
S1 = float(os.environ.get("BLAST_P10_S1", "0.3"))
LIVE_P = float(os.environ.get("BLAST_P10_LIVE_P", "0.5"))


def plan_p10(data_dir: Path, scheduler: str, worker_count: int) -> list[CommitPlan]:
    tests = load_tests(data_dir / "tests.json")
    by_number = {test["number"]: test for test in tests}
    durations = durations_ms(tests)
    diffs = read_patches(data_dir / "commits")
    selected = _select_batch(diffs, tests, data_dir)
    plans: list[CommitPlan] = []
    for commit, diff in diffs:
        numbers, fallback, notes = selected[commit]
        workers = assign(numbers, by_number, durations, worker_count, scheduler)
        plans.append(
            CommitPlan(
                commit=commit,
                workers=workers,
                selected=numbers,
                fallback=fallback,
                notes=notes,
                comment_only=comment_only_heuristic(diff),
            )
        )
    return plans


def _select_batch(diffs, tests, data_dir: Path) -> dict:
    every = [test["number"] for test in tests]
    try:
        eb, fp, it, ac = _load_research(tests, data_dir)
    except Exception as error:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {commit: (every, True, [f"p10: setup failed ({type(error).__name__}: {error})"]) for commit, _ in diffs}

    src = eb.src
    jobs = []
    out = {}
    for commit, diff in diffs:
        try:
            parsed = eb.parse_patch(diff)
            if not parsed or _unsafe(parsed, src, diff):
                out[commit] = (every, True, ["p10: fallback unsafe diff"])
                continue
            files = {}
            for item in parsed:
                if item["new"] == "/dev/null":
                    continue
                base = src / item["new"]
                files[item["new"]] = eb.apply(base.read_text(encoding="utf-8"), item)
            if _identity_noop(parsed, src, files):
                # is/== (or is not/!=) where every stored value makes the two operators agree.
                # Probing that commit only spends CPU; the schedule stays empty.
                print(f"{commit} p10: identity-noop", file=sys.stderr, flush=True)
                out[commit] = ([], False, ["p10: identity-noop"])
                continue
            cand, notes = eb.select(diff, "stmt+import")
            jobs.append((commit, set(cand), files, parsed, list(notes)))
        except Exception as error:
            out[commit] = (every, True, [f"p10: fallback {type(error).__name__}"])

    if not jobs:
        return out
    if not hasattr(os, "fork") or not sys.platform.startswith("linux"):
        for commit, cand, _files, _parsed, notes in jobs:
            out[commit] = (sorted(cand), False, ["p10: probing unavailable -> static", *_fmt(notes)])
        return out

    by = {test["number"]: test for test in eb.tests}
    cost = _cpu_table(by)
    def tcost(number):
        return cost.get(number, by[number]["seconds"])

    def sim(numbers):
        return eb.schedule(numbers)[0] / 1000

    mods = sorted({
        by[number]["file"][:-3].replace("/", ".")
        for _c, cand, _f, _p, _n in jobs
        for number in cand
        if tcost(number) <= B or by[number]["seconds"] <= B
    })
    fp.warm(str(src), mods)
    need = sorted({
        (by[number]["file"][:-3].replace("/", "."), by[number]["id"].split("::")[1])
        for _c, cand, _f, _p, _n in jobs
        for number in cand
        if tcost(number) <= B or by[number]["seconds"] <= B
    })
    preset_path = os.environ.get("PRESET_CACHE", "")
    if os.environ.get("PRESET", "1") == "1" and preset_path and os.path.exists(preset_path):
        fp.load_presets(preset_path, need)

    graph = _reverse_imports()

    def run(files, numbers, plan):
        if not numbers:
            return set()
        result = fp.probe(files, [by[number]["id"] for number in numbers], plan)
        return {number for number in numbers if result.get(by[number]["id"]) != "pass"}

    for commit, cand, files, parsed, notes in jobs:
        kind = "fixture" if parsed and all(item["new"].startswith("tests/fixtures/") for item in parsed) else "code"
        cheap = sorted((number for number in cand if by[number]["seconds"] <= B), key=tcost)
        big = set(cand) - set(cheap)
        bigcheap = sorted((number for number in big if tcost(number) <= B), key=tcost)
        imp_fail: set = set()
        imp_note = ""
        if _import_affecting(parsed, src, eb, ac, it):
            imp_fail, bad = _import_failures(files, src, eb, it, fp, graph, by)
            imp_note = f" import-probe:{len(bad)}"
            if len(imp_fail) > 400:
                # a broken import failed collection across the suite: run everything
                out[commit] = (every, True, ["p10: import probe wide", *_fmt(notes)])
                continue
        if not cand and not imp_fail:
            sel, how = set(), "empty"
        elif not cheap:
            sel, how = set(cand), "only-big"
        else:
            plan = fp.prepare(files)

            def cpu_est(numbers):
                return (sum(tcost(number) for number in numbers) + OVH) if numbers else 0.0

            live_save = max(0.0, sim(cand) - min(sim({number}) for number in cand))
            if not big and cpu_est(cheap) >= P_PASS[kind] * sim(cand) + (1 - P_PASS[kind]) * live_save:
                # A numeric change in a dialect file fans out to every user of that dialect. Probing the
                # dialect's own test module is a couple of seconds; if those pass, the rest of the fan-out
                # did not observe the new value. Including all of them is the 9 second commit.
                witness = _dialect_witness(parsed, cheap, by, src, files)
                if witness and cpu_est(witness) < sim(set(cheap)):
                    if run(files, witness, plan):
                        sel, how = set(cand), "include-all(EV)"
                    else:
                        sel, how = set(), "dialect-witness-pass"
                else:
                    sel, how = set(cand), "include-all(EV)"
            else:
                stage, acc = [], 0.0
                for number in cheap:
                    if stage and acc + tcost(number) > S1:
                        break
                    stage.append(number)
                    acc += tcost(number)
                failed = run(files, stage, plan)
                rest = [number for number in cheap if number not in stage]

                def decide(fixed, pending, tag):
                    if not pending:
                        return fixed, tag
                    marginal = sim(fixed | set(pending)) - sim(fixed)
                    if LIVE_P * marginal <= cpu_est(pending):
                        return fixed | set(pending), tag + ":incl"
                    return fixed | run(files, pending, plan), tag + ":probe"

                if failed:
                    sel, how = decide(failed | (big - set(bigcheap)), rest + bigcheap, "live")
                elif rest:
                    found = run(files, rest, plan)
                    if found:
                        sel, how = decide(found | (big - set(bigcheap)), bigcheap, "stage2-live")
                    else:
                        sel, how = set(), "all-pass"
                else:
                    sel, how = set(), "all-pass(s1)"
        sel = sel | imp_fail
        line = f"p10: {how}{imp_note} cand={len(cand)} sel={len(sel)}"
        print(f"{commit} {line} modes={dict(fp.STATS)}", file=sys.stderr, flush=True)
        out[commit] = (sorted(sel), False, [line, *_fmt(notes)])
    try:
        import resource
        self_use = resource.getrusage(resource.RUSAGE_SELF)
        child_use = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = self_use.ru_utime + self_use.ru_stime + child_use.ru_utime + child_use.ru_stime
        print(f"CPU {cpu:.3f}", file=sys.stderr, flush=True)
    except ImportError:
        pass
    return out


def _fmt(notes) -> list[str]:
    pretty = []
    for note in notes[:6]:
        pretty.append(note if isinstance(note, str) else " ".join(str(part) for part in note))
    return pretty


def _unsafe(parsed, src: Path, diff: str) -> bool:
    if "GIT binary patch" in diff or diff.startswith("Binary files"):
        return True
    for item in parsed:
        if item["old"] == "/dev/null" or item["new"] == "/dev/null":
            return True
        path = item["new"]
        if path.endswith(".py") and path.startswith("sqlglot/") and not (src / path).is_file():
            return True
    return False


def _import_affecting(parsed, src, eb, ac, it) -> bool:
    """True when the patch can make an import fail. Dict-value edits cannot."""
    for item in parsed:
        path = item["new"] if item["new"] != "/dev/null" else item["old"]
        if not (path.endswith(".py") and path.startswith("sqlglot/")):
            continue
        old = (src / path).read_text(encoding="utf-8")
        new = eb.apply(old, item)
        try:
            ac.parse(new)
        except SyntaxError:
            return True
        if ac.dump(new) == ac.dump(old):
            continue
        # Only constant values changed (ints, floats, bools). That cannot make the import fail,
        # so the import probe would spend CPU and add nothing. String constants can be module paths.
        if _numeric_constants_only(old, new):
            continue
        if ac.sdump(old) != ac.sdump(new):
            return True
        if any(it.import_line(path, line) for line in eb.touched_lines(item)):
            return True
    return False


class _BlankConsts(ast.NodeTransformer):
    def visit_Constant(self, node):
        node.value = None
        return node


def _dialect_witness(parsed, cand, by, src, files) -> list:
    """Candidates that live in the dialect's own test module, for a numbers-only dialect edit."""
    py = [item for item in parsed if item["new"].endswith(".py")]
    if not py or any(not item["new"].startswith("sqlglot/dialects/") or item["new"].endswith("/__init__.py") for item in py):
        return []
    stems = []
    for item in parsed:
        path = item["new"]
        if not path.endswith(".py"):
            continue
        old_path = src / path
        if not old_path.is_file() or path not in files:
            return []
        if not _numeric_constants_only(old_path.read_text(encoding="utf-8"), files[path]):
            return []
        stems.append(Path(path).stem)
    if not stems:
        return []
    wanted = {f"tests/dialects/test_{stem}.py" for stem in stems}
    return [number for number in cand if by[number]["file"] in wanted]


def _numeric_constants_only(old: str, new: str) -> bool:
    try:
        old_tree, new_tree = ast.parse(old), ast.parse(new)
    except SyntaxError:
        return False
    old_consts = [node.value for node in ast.walk(old_tree) if isinstance(node, ast.Constant)]
    new_consts = [node.value for node in ast.walk(new_tree) if isinstance(node, ast.Constant)]
    if len(old_consts) != len(new_consts):
        return False
    changed = False
    for before, after in zip(old_consts, new_consts):
        if before == after and type(before) is type(after):
            continue
        changed = True
        if isinstance(before, bool) or isinstance(after, bool):
            if type(before) is not bool or type(after) is not bool:
                return False
            continue
        if not isinstance(before, (int, float, type(None))) or not isinstance(after, (int, float, type(None))):
            return False
    if not changed:
        return False
    return ast.dump(_BlankConsts().visit(old_tree)) == ast.dump(_BlankConsts().visit(new_tree))


_CMP_PAIRS = {
    (ast.Is, ast.Eq),
    (ast.Eq, ast.Is),
    (ast.IsNot, ast.NotEq),
    (ast.NotEq, ast.IsNot),
}
_REPO: dict = {}


def _identity_noop(parsed, src: Path, files: dict) -> bool:
    """True when every edit is an identity/equality swap that cannot change a result.

    `x is Enum.MEMBER` and `x == Enum.MEMBER` agree when x is always a member.
    `x is None` and `x == None` agree when x is always None or an instance whose
    class uses identity equality. Anything else keeps the probe.
    """
    try:
        if not parsed:
            return False
        for item in parsed:
            path = item["new"]
            if not path.endswith(".py") or not path.startswith("sqlglot/") or path not in files:
                return False
            old = (src / path).read_text(encoding="utf-8")
            if not _file_identity_noop(old, files[path], src, path):
                return False
        return True
    except Exception:
        return False


def _file_identity_noop(old: str, new: str, src: Path, rel: str) -> bool:
    old_tree, new_tree = ast.parse(old), ast.parse(new)
    module = rel[:-3].replace("/", ".")
    local = _bindings(old_tree, module)
    sites: list = []

    def walk(left, right, class_q):
        if isinstance(left, ast.ClassDef) and isinstance(right, ast.ClassDef):
            if left.name != right.name or len(left.body) != len(right.body):
                return False
            if _dump(left.bases) != _dump(right.bases) or _dump(left.decorator_list) != _dump(right.decorator_list):
                return False
            qname = local.get(left.name, f"{module}.{left.name}")
            return all(walk(a, b, qname) for a, b in zip(left.body, right.body))
        if isinstance(left, ast.Compare) and isinstance(right, ast.Compare):
            if (
                len(left.ops) == 1
                and len(right.ops) == 1
                and type(left.ops[0]) is not type(right.ops[0])
            ):
                if (type(left.ops[0]), type(right.ops[0])) not in _CMP_PAIRS:
                    return False
                if _dump(left.left) != _dump(right.left) or _dump(left.comparators) != _dump(right.comparators):
                    return False
                sites.append((left, class_q))
                return True
        if type(left) is not type(right):
            return False
        if isinstance(left, ast.AST):
            for (name_l, value_l), (name_r, value_r) in zip(ast.iter_fields(left), ast.iter_fields(right), strict=True):
                if name_l != name_r or not walk(value_l, value_r, class_q):
                    return False
            return True
        if isinstance(left, list):
            return len(left) == len(right) and all(walk(a, b, class_q) for a, b in zip(left, right))
        return left == right

    if not walk(old_tree, new_tree, None) or not sites:
        return False
    repo = _repo(src)
    return all(repo.site_ok(compare, class_q, local) for compare, class_q in sites)


def _dump(node) -> str:
    if isinstance(node, ast.AST):
        return ast.dump(node, include_attributes=False)
    if isinstance(node, list):
        return "[" + ",".join(_dump(item) for item in node) + "]"
    return repr(node)


def _bindings(tree: ast.AST, module: str) -> dict:
    local: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            parts = module.split(".")
            if node.level:
                parts = parts[: len(parts) - node.level]
            base = ".".join([*parts, *node.module.split(".")] if node.level else [node.module])
            for alias in node.names:
                if alias.name == "*":
                    continue
                local[alias.asname or alias.name] = f"{base}.{alias.name}"
        elif isinstance(node, ast.ClassDef):
            local[node.name] = f"{module}.{node.name}"
    return local


class _Repo:
    def __init__(self, src: Path):
        self.classes: dict = {}
        pending = []
        for path in (src / "sqlglot").rglob("*.py"):
            module = path.relative_to(src).with_suffix("").as_posix().replace("/", ".")
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            local = _bindings(tree, module)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    pending.append((node, local, module))
        for node, local, module in pending:
            self.classes[f"{module}.{node.name}"] = self._class(node, local)

    def _class(self, node: ast.ClassDef, local: dict) -> dict:
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(local.get(base.id, base.id))
            else:
                bases.append("UNKNOWN")
        class_assigns: dict = {}
        self_assigns: dict = {}
        has_eq = False
        for stmt in node.body:
            if isinstance(stmt, ast.ClassDef):
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if stmt.name in ("__eq__", "__ne__"):
                    has_eq = True
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Assign):
                        for target in sub.targets:
                            attr = _self_attr(target)
                            if attr:
                                self_assigns.setdefault(attr, []).append((stmt, sub.value, None))
                    elif isinstance(sub, ast.AnnAssign):
                        attr = _self_attr(sub.target)
                        if attr:
                            self_assigns.setdefault(attr, []).append((stmt, sub.value, sub.annotation))
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        class_assigns[target.id] = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                class_assigns[stmt.target.id] = stmt.value
        return {
            "bases": bases,
            "local": local,
            "class_assigns": class_assigns,
            "self_assigns": self_assigns,
            "has_eq": has_eq,
        }

    def site_ok(self, compare: ast.Compare, class_q, local: dict) -> bool:
        if len(compare.comparators) != 1:
            return False
        sides = [compare.left, compare.comparators[0]]
        if any(_is_none(side) for side in sides):
            other = sides[1] if _is_none(sides[0]) else sides[0]
            attr = _self_attr(other)
            return bool(attr and class_q and self._none_safe(class_q, attr))
        enum_q = None
        attr = None
        for side in sides:
            found = self._enum_member(side, local)
            if found:
                enum_q = found
            name = _self_attr(side)
            if name:
                attr = name
        if enum_q and attr and class_q:
            return self._enum_safe(class_q, attr, enum_q)
        members = [self._enum_member(side, local) for side in sides]
        return bool(members[0] and members[1] and members[0] == members[1])

    def _enum_member(self, node, local: dict):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            return None
        qname = local.get(node.value.id)
        if not qname or not self._is_enum(qname) or node.attr not in self.classes.get(qname, {}).get("class_assigns", {}):
            return None
        return qname

    def _is_enum(self, qname: str, seen=None) -> bool:
        if qname in ("enum.Enum", "enum.IntEnum", "enum.StrEnum", "enum.Flag"):
            return True
        info = self.classes.get(qname)
        if not info:
            return False
        seen = set() if seen is None else seen
        if qname in seen:
            return False
        seen.add(qname)
        return any(self._is_enum(base, seen) for base in info["bases"])

    def _enum_safe(self, class_q: str, attr: str, enum_q: str) -> bool:
        group = self._family(class_q)
        saw = False
        for qname in group:
            info = self.classes.get(qname)
            if not info:
                return False
            for _func, value, _ann in info["self_assigns"].get(attr, ()):
                saw = True
                if value is None or not self._member_expr(value, info["local"], qname, enum_q):
                    return False
        return saw

    def _member_expr(self, expr, local: dict, class_q: str, enum_q: str) -> bool:
        if isinstance(expr, ast.Call) and self._resolve_name(expr.func, local) == enum_q:
            return True
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            if expr.value.id == "self":
                return self._classvars_are_members(class_q, expr.attr, enum_q)
            if local.get(expr.value.id) == enum_q and expr.attr in self.classes[enum_q]["class_assigns"]:
                return True
        return False

    def _classvars_are_members(self, class_q: str, name: str, enum_q: str) -> bool:
        for qname in self._family(class_q):
            found = self._classvar(qname, name)
            if found is None:
                return False
            value, local = found
            if not self._direct_member(value, local, enum_q):
                return False
        return True

    def _direct_member(self, expr, local: dict, enum_q: str) -> bool:
        if isinstance(expr, ast.Call) and self._resolve_name(expr.func, local) == enum_q:
            return True
        if (
            isinstance(expr, ast.Attribute)
            and isinstance(expr.value, ast.Name)
            and local.get(expr.value.id) == enum_q
            and expr.attr in self.classes.get(enum_q, {}).get("class_assigns", {})
        ):
            return True
        return False

    def _classvar(self, qname: str, name: str, seen=None):
        info = self.classes.get(qname)
        if not info:
            return None
        if name in info["class_assigns"]:
            return info["class_assigns"][name], info["local"]
        seen = set() if seen is None else seen
        if qname in seen:
            return None
        seen.add(qname)
        for base in info["bases"]:
            found = self._classvar(base, name, seen)
            if found:
                return found
        return None

    def _family(self, class_q: str) -> list:
        return [class_q, *self._ancestors(class_q), *self._descendants(class_q)]

    def _ancestors(self, qname: str, seen=None) -> list:
        info = self.classes.get(qname)
        if not info:
            return []
        seen = set() if seen is None else seen
        out = []
        for base in info["bases"]:
            if base in seen or base not in self.classes:
                continue
            seen.add(base)
            out.append(base)
            out.extend(self._ancestors(base, seen))
        return out

    def _descendants(self, ancestor: str) -> list:
        return [qname for qname in self.classes if qname != ancestor and self._inherits(qname, ancestor)]

    def _inherits(self, qname: str, ancestor: str, seen=None) -> bool:
        info = self.classes.get(qname)
        if not info:
            return False
        seen = set() if seen is None else seen
        if qname in seen:
            return False
        seen.add(qname)
        for base in info["bases"]:
            if base == ancestor or self._inherits(base, ancestor, seen):
                return True
        return False

    def _none_safe(self, class_q: str, attr: str) -> bool:
        group = [class_q, *self._descendants(class_q)]
        tracked = []
        others = []
        for qname in group:
            info = self.classes.get(qname)
            if not info:
                return False
            for name, entries in info["self_assigns"].items():
                for func, value, ann in entries:
                    bucket = tracked if name == attr else others
                    bucket.append((info, func, name, value, ann))
        if not tracked:
            return False
        self_types: dict = {}
        for info, _func, name, _value, ann in [*tracked, *others]:
            if ann is None:
                continue
            parsed = _ann_types(ann, info["local"])
            if name == attr and (not parsed or not self._atoms_identity(parsed)):
                return False
            if parsed:
                self_types[name] = parsed
        for _ in range(len(tracked) + len(others) + 2):
            progressed = False
            for info, func, name, value, _ann in [*others, *tracked]:
                if value is None:
                    continue
                got = _expr_types(value, _arg_types(func, info["local"]), self_types)
                if not got or got == self_types.get(name):
                    continue
                self_types[name] = got if name not in self_types else self_types[name] | got
                progressed = True
            if not progressed:
                break
        for info, func, _name, value, ann in tracked:
            if value is None:
                if ann is None:
                    return False
                continue
            got = _expr_types(value, _arg_types(func, info["local"]), self_types)
            if not got or not self._atoms_identity(got):
                return False
        return True

    def _atoms_identity(self, tags: set) -> bool:
        if not tags:
            return False
        for kind, payload in tags:
            if kind != "atom":
                return False
            if payload != "None" and not self._identity_eq(payload):
                return False
        return True

    def _identity_eq(self, qname: str, seen=None) -> bool:
        if qname in ("None", "object"):
            return True
        info = self.classes.get(qname)
        if not info or info["has_eq"]:
            return False
        seen = set() if seen is None else seen
        if qname in seen:
            return False
        seen.add(qname)
        return all(base == "object" or self._identity_eq(base, seen) for base in info["bases"])

    def _resolve_name(self, node, local: dict):
        if isinstance(node, ast.Name):
            return local.get(node.id)
        return None


def _self_attr(node) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return node.attr
    return None


def _is_none(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _ann_types(node, local: dict):
    if isinstance(node, ast.Constant) and node.value is None:
        return {("atom", "None")}
    if isinstance(node, ast.Name):
        if node.id == "None":
            return {("atom", "None")}
        qname = local.get(node.id)
        return {("atom", qname)} if qname else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _ann_types(node.left, local), _ann_types(node.right, local)
        if left is None or right is None:
            return None
        return left | right
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in ("dict", "Dict"):
        slice_node = node.slice
        if isinstance(slice_node, ast.Tuple) and len(slice_node.elts) == 2:
            value = _ann_types(slice_node.elts[1], local)
            return {("dict", frozenset(value))} if value else None
    return None


def _arg_types(func, local: dict) -> dict:
    env = {}
    if func is None:
        return env
    for arg in [*func.args.args, *func.args.posonlyargs, *func.args.kwonlyargs]:
        if arg.annotation:
            parsed = _ann_types(arg.annotation, local)
            if parsed:
                env[arg.arg] = parsed
    return env


def _expr_types(expr, env: dict, self_types: dict):
    if isinstance(expr, ast.Constant) and expr.value is None:
        return {("atom", "None")}
    if isinstance(expr, ast.Name):
        return env.get(expr.id)
    if _self_attr(expr):
        return self_types.get(expr.attr)
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Attribute) and func.attr == "values" and not expr.args:
            inner = _expr_types(func.value, env, self_types)
            if not inner:
                return None
            out: set = set()
            for kind, payload in inner:
                if kind != "dict":
                    return None
                out |= set(payload)
            return out
        if isinstance(func, ast.Name) and func.id == "list" and len(expr.args) == 1:
            return _expr_types(expr.args[0], env, self_types)
    if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant) and type(expr.slice.value) is int:
        return _expr_types(expr.value, env, self_types)
    return None


def _repo(src: Path) -> _Repo:
    key = str(src)
    repo = _REPO.get(key)
    if repo is None:
        repo = _Repo(src)
        _REPO[key] = repo
    return repo


def _import_failures(files, src, eb, it, fp, reverse, by):
    changed = {rel[:-3].replace("/", ".").removesuffix(".__init__") for rel in files if rel.endswith(".py")}
    sqlglot_mods = sorted(
        str(path.relative_to(src))[:-3].replace("/", ".").removesuffix(".__init__")
        for path in (src / "sqlglot").rglob("*.py")
        if path.name != "__main__.py"
    )
    test_mods = sorted({test["file"][:-3].replace("/", ".") for test in eb.tests})
    known = set(sqlglot_mods + test_mods)
    if changed and changed <= known and reverse:
        closure = set(changed)
        stack = list(changed)
        while stack:
            name = stack.pop()
            for parent in reverse.get(name, ()):
                if parent not in closure:
                    closure.add(parent)
                    stack.append(parent)
        order = [name for name in sqlglot_mods + test_mods if name in closure]
        if "sqlglot" in closure or len(order) > 40:
            bad = fp.import_probe(files, sqlglot_mods + test_mods)
        else:
            bad = fp.import_probe_partial(files, order)
    else:
        bad = fp.import_probe(files, sqlglot_mods + test_mods)
    if not bad:
        return set(), []
    if "sqlglot" in bad:
        return set(eb.ALL), bad
    failed = set()
    by_file: dict = {}
    for test in eb.tests:
        by_file.setdefault(test["file"], []).append(test["number"])
    for name in bad:
        if name.startswith("tests."):
            failed.update(by_file.get(name.replace(".", "/") + ".py", []))
            continue
        rel = name.replace(".", "/") + ".py"
        if not (src / rel).exists():
            rel = name.replace(".", "/") + "/__init__.py"
        for _line, numbers in eb.cov.get(rel, {}).items():
            failed.update(numbers)
        if rel.startswith(it.DIALECT_DIRS):
            if "_IDX" not in eb.__dict__:
                eb._IDX = it.Index(src, eb.cov)
            for stem in it.subclasses_stems(src, Path(rel).stem):
                failed.update(eb._IDX.dialect_users(stem))
    return failed, bad


def _reverse_imports() -> dict:
    art = Path(os.environ.get("BR_ARTIFACTS", ""))
    path = art / "import_graph.json"
    if not path.is_file():
        path = ROOT / "research" / "ethan-p10" / "maps" / "import_graph.json"
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    reverse: dict = {}
    for key in ("edges", "lazy_edges"):
        for source, targets in document[key].items():
            for target in targets:
                reverse.setdefault(target, set()).add(source)
    return reverse


def _cpu_table(by: dict) -> dict:
    path = Path(os.environ.get("TEST_CPU", CODE / "test_cpu.json"))
    if not path.is_file():
        return {}
    raw = {int(key): value for key, value in json.loads(path.read_text(encoding="utf-8")).items()}
    ids_path = ROOT / "maps" / "hybrid" / "test_ids.json"
    if not ids_path.is_file():
        return raw
    ids = json.loads(ids_path.read_text(encoding="utf-8"))["tests"]
    by_id = {test["id"]: number for number, test in by.items()}
    mapped = {}
    for index, test_id in enumerate(ids):
        if index in raw and test_id in by_id:
            mapped[by_id[test_id]] = raw[index]
    return mapped or raw


def _load_research(tests, data_dir: Path):
    os.environ.setdefault("BR_IMPDERIV", "1")
    os.environ.setdefault("BR_LAMBDA", "1")
    os.environ["BR_TESTS"] = str(data_dir / "tests.json")
    art = os.environ.get("BR_ARTIFACTS") or os.environ.get("BLAST_P10_ART") or str(ROOT / "maps" / "hybrid")
    os.environ["BR_ARTIFACTS"] = art
    src = os.environ.get("BLAST_SQLGLOT_SRC", "")
    if not src:
        for cand in (Path("/opt/sqlglot"), ROOT / ".cache" / "sqlglot"):
            if (cand / "sqlglot" / "__init__.py").is_file():
                src = str(cand)
                break
    os.environ.setdefault("BR_IDXCACHE", str(Path(art) / "idx_cache.pkl"))
    os.environ.setdefault("PRESET_CACHE", str(Path(art) / "preset_cache.pkl"))
    os.environ.setdefault("TEST_CPU", str(CODE / "test_cpu.json"))
    code = str(CODE)
    if code not in sys.path:
        sys.path.insert(0, code)
    sys.argv = ["eval_baselines.py", str(ROOT), src, art]
    import astcache
    import eval_baselines
    import fastprobe
    import importtime

    # the research loader captured tests at import; keep its numbers aligned with this run
    if [test["id"] for test in eval_baselines.tests] != [test["id"] for test in tests]:
        raise RuntimeError("test catalog does not match the maps")
    return eval_baselines, fastprobe, importtime, astcache
