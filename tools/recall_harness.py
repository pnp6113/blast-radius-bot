#!/usr/bin/env python3
"""LOCAL VALIDATION ONLY (runs inside the image: Linux fork). Ground truth for each mutant = probe every test except
the 3 heaviest; compare with a strategy's selection. Prints misses; writes jsonl.
    docker run ... -v MUTANTS:/mut:ro -v OUT:/out -v TOOLS:/work/tools:ro IMAGE \
        python /work/tools/recall_harness.py /mut /out/recall.jsonl STRATEGY WORKERS
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
HEAVY_IDS = {"tests/test_parser.py::TestParser::test_max_nodes", "tests/test_executor.py::TestExecutor::test_execute_tpcds",
             "tests/test_generator.py::TestGenerator::test_generator_memory"}
TESTS = json.loads(Path(os.environ.get("BLAST_DATA", "/data"), "tests.json").read_text())["tests"]


def work(args):
    pf, strategy = args
    from blast import patchparse, probe
    from blast.select_hybrid import select_hybrid
    from blast.select_p2 import select_p2
    patch = Path(pf).read_text(encoding="utf-8")
    t0 = time.monotonic()
    sel, fb, notes = (select_p2 if strategy == "p2" else select_hybrid)(patch, TESTS)
    t_sel = time.monotonic() - t0
    src = Path(os.environ.get("BLAST_SQLGLOT_SRC", "/opt/sqlglot"))
    files = {}
    for fp in patchparse.parse(patch):
        files[fp.path] = patchparse.apply((src / fp.old_path).read_text(encoding="utf-8"), fp)
    allt = [(t["id"], t["seconds"]) for t in TESTS if t["id"] not in HEAVY_IDS]
    res = probe.probe(str(src), files, allt, deadline=time.monotonic() + 900, factor=6.0, slack=20.0)
    num = {t["id"]: t["number"] for t in TESTS}
    fails = sorted(num[i] for i, r in res.items() if r != "pass")
    missed = sorted(set(fails) - set(sel))
    return {"mutant": Path(pf).name, "sel": len(sel), "fallback": fb, "fail": fails, "missed": missed,
            "t_sel": round(t_sel, 2), "notes": [str(n)[:120] for n in notes][:4]}


def main():
    mdir, outp, strategy, w = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    from blast import probe
    probe.warm()
    os.environ["BLAST_PROBE_FRACTION"] = "50"  # no batch deadline for validation runs
    files = sorted(str(p) for p in Path(mdir).glob("*.patch"))
    n_miss = 0
    with mp.get_context("fork").Pool(w) as pool, open(outp, "w") as fo:
        for r in pool.imap_unordered(work, [(f, strategy) for f in files]):
            fo.write(json.dumps(r) + "\n")
            fo.flush()
            n_miss += bool(r["missed"])
            print(f"{r['mutant']:34s} sel={r['sel']:5d} fail={len(r['fail']):4d} "
                  f"{'MISS ' + str(r['missed'][:6]) if r['missed'] else ''}", flush=True)
    print(f"### {strategy}: {len(files)} mutants, {n_miss} commit-level misses")


if __name__ == "__main__":
    main()
