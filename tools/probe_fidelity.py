#!/usr/bin/env python3
"""LOCAL VALIDATION ONLY (inside the image). Probe vs real pytest on patched copies of the pinned tree.

For each mutant: probe the given tests (default: the tests the full-suite probe found failing, plus up to 40 passing
candidates); then copy /opt/sqlglot to a temp dir, apply the patch on disk, run real pytest on those test ids
(SKIP_INTEGRATION=0, -p no:cacheprovider) and compare per-test outcomes. Any disagreement is printed.
    python /work/tools/probe_fidelity.py /mut/patches /mut/out/p2.jsonl /tmp/work N
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, "/app")
from blast import patchparse, probe  # noqa: E402

TESTS = json.loads(Path("/data/tests.json").read_text())["tests"]
BY_NUM = {t["number"]: t for t in TESTS}
SRC = Path(os.environ.get("BLAST_SQLGLOT_SRC", "/opt/sqlglot"))


def pytest_outcomes(tree: Path, ids: list[str]) -> dict:
    junit = tree / "junit.xml"
    env = dict(os.environ, SKIP_INTEGRATION="0", PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--timeout=300",
                    f"--junitxml={junit}", *ids], cwd=tree, env=env, capture_output=True, timeout=1800)
    out = {i: "pass" for i in ids}
    if not junit.exists():
        return {i: "fail" for i in ids}
    for case in ET.parse(junit).getroot().iter("testcase"):
        cls = case.get("classname", "")  # tests.test_x.TestX
        mod, _, klass = cls.rpartition(".")
        tid = f"{mod.replace('.', '/')}.py::{klass}::{case.get('name')}"
        bad = any(ch.tag in ("failure", "error") for ch in case)
        if tid in out and bad:
            out[tid] = "fail"
    # collection errors: junit reports them as errors without matching ids; treat ids absent from junit as fail
    seen = {f"{c.get('classname','').rpartition('.')[0].replace('.', '/')}.py::{c.get('classname','').rpartition('.')[2]}::{c.get('name')}"
            for c in ET.parse(junit).getroot().iter("testcase")}
    for i in ids:
        if i not in seen:
            out[i] = "fail"
    return out


def main():
    mdir, results, work, n = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), int(sys.argv[4])
    rows = [json.loads(l) for l in open(results)]
    live = [r for r in rows if r.get("fail") and not r.get("skipped")]
    random.Random(5).shuffle(live)
    probe.warm()
    disagreements = 0
    for r in live[:n]:
        patch = (mdir / r["mutant"]).read_text(encoding="utf-8")
        fps = patchparse.parse(patch)
        files = {fp.path: patchparse.apply((SRC / fp.old_path).read_text(encoding="utf-8"), fp) for fp in fps}
        failing = [BY_NUM[x]["id"] for x in r["fail"]][:60]
        passing_pool = [t["id"] for t in TESTS if t["number"] not in set(r["fail"]) and t["seconds"] < 2.0]
        random.Random(r["mutant"]).shuffle(passing_pool)
        ids = failing + passing_pool[:20]
        pr = probe.probe(str(SRC), files, [(i, next(t["seconds"] for t in TESTS if t["id"] == i)) for i in ids],
                         deadline=time.monotonic() + 900, factor=6.0, slack=20.0)
        tree = work / r["mutant"].replace(".patch", "")
        if tree.exists():
            shutil.rmtree(tree)
        shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__"))
        for rel, text in files.items():
            (tree / rel).write_text(text, encoding="utf-8")
        pt = pytest_outcomes(tree, ids)
        diff = [i for i in ids if pr.get(i) != pt.get(i)]
        disagreements += bool(diff)
        print(f"{r['mutant']:32s} tests={len(ids):3d} probe_fail={sum(v=='fail' for v in pr.values()):3d} "
              f"pytest_fail={sum(v=='fail' for v in pt.values()):3d} {'DISAGREE ' + str([(d, pr.get(d), pt.get(d)) for d in diff[:3]]) if diff else 'agree'}",
              flush=True)
        shutil.rmtree(tree, ignore_errors=True)
    print(f"### fidelity: {min(n, len(live))} mutants, {disagreements} with disagreements")


if __name__ == "__main__":
    main()
