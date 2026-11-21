"""Strategy `p2`: static hybrid candidates, then Ethan's cost-aware probe policy (P2).

Per commit: tests > B seconds are never probed; they are included iff the change is shown live (a cheap probe failed)
or no cheap candidate exists. A cheap stage-1 prefix (<= 0.3 s recorded) is probed first; the rest is either
included directly (when it fits under the makespan anyway) or probed. A commit whose candidates all pass => nothing.

Safety: static fallbacks (every test) are never probed; probing only on Linux with fork; a global wall-clock deadline
(55% of the batch limit: 3 min for <=45 commits, 4.5 min above) after which commits are answered statically; any
probe crash/timeout => those tests count as failing (included).
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

from blast import patchparse, probe
from blast.select_hybrid import _load, select_hybrid

B = float(os.environ.get("BLAST_P2_B", "2.0"))
ALPHA = float(os.environ.get("BLAST_P2_ALPHA", "1.3"))
OVH = 0.15
P_PASS = {"code": 0.26, "fixture": 0.05}
STAGE1_S = 0.3
_RUN: dict = {}


def _deadline() -> float:
    if "deadline" not in _RUN:
        data = Path(os.environ.get("BLAST_DATA", "/data"))
        n = len(list((data / "commits").glob("*.patch"))) if (data / "commits").is_dir() else 40
        limit = 180.0 if n <= 45 else 270.0
        frac = float(os.environ.get("BLAST_PROBE_FRACTION", "0.55"))
        _RUN["deadline"] = time.monotonic() + frac * limit
    return _RUN["deadline"]


def _cost(sel, dur) -> float:
    sel = sorted(set(sel), key=lambda n: -dur[n])
    if not sel:
        return 0.0
    best = None
    for k in range(1, min(8, len(sel)) + 1):
        loads = [0] * k
        for n in sel:
            i = loads.index(min(loads))
            loads[i] += dur[n]
        c = max(loads) + 2000 * k
        best = c if best is None else min(best, c)
    return best / 1000.0


def select_p2(diff: str, tests: list[dict]) -> tuple[list[int], bool, list[str]]:
    cand, fallback, notes = select_hybrid(diff, tests)
    if fallback or not cand:
        return cand, fallback, notes
    if not probe.available() or os.environ.get("BLAST_PROBE", "1") == "0":
        return cand, False, notes + ["p2: probing unavailable -> static"]
    deadline = _deadline()
    if time.monotonic() >= deadline:
        return cand, False, notes + ["p2: budget spent -> static"]
    st = _load(tests)
    by_num = {t["number"]: t for t in tests}
    dur = {n: math.floor(by_num[n]["seconds"] * 1000 + 0.5) for n in by_num}
    secs = {n: by_num[n]["seconds"] for n in by_num}
    try:
        fps = patchparse.parse(diff)
        files = {}
        for fp in fps:
            base = (st["src"] / fp.old_path).read_text(encoding="utf-8")
            files[fp.path] = patchparse.apply(base, fp)
    except Exception as e:  # noqa: BLE001
        return cand, False, notes + [f"p2: cannot build overlay ({type(e).__name__}) -> static"]
    kind = "fixture" if all(fp.path.startswith("tests/fixtures/") for fp in fps) else "code"
    probe.warm()

    def cpu_est(ns):
        return (ALPHA * sum(secs[n] for n in ns) + OVH) if ns else 0.0

    def run(ns):
        if not ns:
            return set()
        res = probe.probe(str(st["src"]), files, [(by_num[n]["id"], secs[n]) for n in ns], deadline=deadline + 20.0)
        return {n for n in ns if res.get(by_num[n]["id"]) != "pass"}

    S = lambda s: _cost(s, dur)  # noqa: E731
    cheap = sorted((n for n in cand if secs[n] <= B), key=lambda n: secs[n])
    big = set(cand) - set(cheap)
    if cpu_est(cheap) > P_PASS[kind] * S(cand) and not big:
        return cand, False, notes + ["p2: include all (probe not worth it)"]
    s1, acc = [], 0.0
    for n in cheap:
        if s1 and acc + secs[n] > STAGE1_S:
            break
        s1.append(n)
        acc += secs[n]
    failed = run(s1)
    rest = [n for n in cheap if n not in s1]
    live = bool(failed) or not cheap
    must = failed | (big if live else set())
    if live and rest:
        if S(must | set(rest)) - S(must) <= cpu_est(rest):
            sel, how = must | set(rest), "live: include rest"
        else:
            sel, how = must | run(rest), "live: probe rest"
    elif rest:
        f2 = run(rest)
        sel, how = (f2 | failed | big, "stage2 live") if f2 else (set(), "all pass")
    else:
        sel, how = must, "stage1 only"
    return sorted(sel), False, notes + [f"p2: {how} cand={len(cand)} sel={len(sel)}"]
