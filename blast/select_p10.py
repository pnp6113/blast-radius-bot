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
            cand, notes = eb.select(diff, "stmt+import")
            files = {}
            for item in parsed:
                if item["new"] == "/dev/null":
                    continue
                base = src / item["new"]
                files[item["new"]] = eb.apply(base.read_text(encoding="utf-8"), item)
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
