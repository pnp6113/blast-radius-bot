# Blast Radius — research prototype handoff (Team Last Mech Standing)

Prepared by Ethan, Nov 21 2026. This is a **research prototype**, not a submission. Nothing here was pushed to
`pnp6113/blast-radius-bot` or submitted. Read `blast-radius-report.md` for the full design, scoring math, and the Claude/Codex briefs.

## Pins
- sqlglot: `eb11aec4be8cb2122afe713a4816662c3f0fb2fa` (also in `SQLGLOT_PIN`)
- Python 3.13.5 (official image is `python:3.13-slim`, pinned digest in `kit/Dockerfile`)
- Official deps: `kit/requirements.lock` (authoritative). Exact research env: `env-pip-freeze.txt` (coverage 7.17.4, pytest 9.2.0, pytest-xdist 3.8.0, pytest-timeout 2.4.0).

## Layout
| Path | What |
|---|---|
| `kit/` | Official starter kit as downloaded (SCORING.md, score.py, Dockerfile, Makefile, sample patches/tests/answers) |
| `scripts/eval_baselines.py` | **Static hybrid** selector + baselines (`select(patch, 'stmt+import')`) — reproduces 174.818 s |
| `scripts/importtime.py` | Import-time name index: changed module/class-body names → readers → tests, narrowed by class + dialect |
| `scripts/probe.py` | Runtime probe: runs candidate tests against the patched sqlglot in a forked child with an in-memory overlay (no disk writes) |
| `scripts/eval_policy.py` | **P1**: static → probe all candidates ≤2 s → include >2 s candidates iff a probe failed |
| `scripts/eval_policy2.py` | **P2**: cost-aware probe/no-probe rule (≈136 s) |
| `scripts/eval_probe.py` | Probe fidelity vs official labels |
| `scripts/build_covmap.py` | coverage.py context DB → `covmap_line.json` |
| `scripts/import_graph.py` | AST import graph → `import_graph.json` |
| `plugins/syncpool.py` | pytest plugin used during coverage: runs ProcessPoolExecutor work in-process so per-test contexts see it (without it, file-level coverage misses tests) |
| `plugins/dialectrec.py`, `plugins/openrec.py` | pytest plugins recording which Dialect classes / fixture files each test uses (raw per-worker outputs `*_gw*.json` included) |
| `scripts/mutants.py`, `mutants_importtime.py`, `calibrate.py`, `recall_full.py`, `recall_report.py` | Synthetic validation (local only; never ship mutant-derived tables — rule risk) |
| `artifacts/` | Maps: `covmap_line.json(.gz)`, `covmap_compact.json(.gz)`, `dialect_usage_map.json`, `file_reads_map.json` (fixture reads), `import_graph.json`, `fanin_per_file.json`, `test_timings.csv`, `coveragerc` |
| `results/` | Raw results: `eval_policy_B2.txt` (P1), `eval_policy2_B2.txt` (P2), `calibration_mutants.jsonl`, `recall_full.jsonl`, `recall_full2.jsonl`, full-suite junit + timings |

Excluded: sqlglot checkout (clone it at the pin), venv, coverage `.coverage` DB (6.6 MB, regenerable), generated mutant patches, caches.

## Setup
```bash
git clone https://github.com/tobymao/sqlglot && cd sqlglot && git checkout eb11aec4be8cb2122afe713a4816662c3f0fb2fa && cd ..
python3.13 -m venv venv && . venv/bin/activate
pip install -r kit/requirements.lock   # if anything is missing, match env-pip-freeze.txt
pip install -e ./sqlglot
cd artifacts && gunzip -k covmap_line.json.gz covmap_compact.json.gz && cd ..
```

## Reproduce the static hybrid (174.818 s, 0 misses)
```bash
python scripts/eval_baselines.py kit ./sqlglot artifacts | grep '###'
```
Expected (verified from this package on Nov 21):
```
### full: simulated=1104.510s misses=0
### file: simulated=534.316s misses=1
### line: simulated=753.879s misses=2
### stmt: simulated=454.325s misses=1
### stmt+import: simulated=174.818s misses=0
```
CPU: static selection itself is <1 s for all 30 commits (whole script ~3.6 s wall incl. loading the 14 MB JSON; a shipped selector should load a compact/pickled map at build time).

## Reproduce P1 (≈101.0 s simulated + 48.3 s CPU ≈ 149.4 s) and P2 (≈119.9 s + 16.3 s ≈ 136.3 s)
```bash
python scripts/eval_policy.py  kit ./sqlglot artifacts 2.0        # P1, B = 2 s probe threshold
python scripts/eval_policy2.py kit ./sqlglot artifacts 2.0 1.3    # P2, B = 2 s, alpha = 1.3
```
**Exact CPU accounting caveats (read these):**
- The printed CPU is `CPU(after warm imports)`: user+sys of the probe children *after* sqlglot and the test modules are already imported. It does **not** include Python startup, importing sqlglot/tests, or loading the maps. The official score charges **all** CPU in the container from start to exit, so real totals will be higher by that one-time cost (measure it in Docker — experiment E2).
- Numbers are box CPU. The recorded test seconds are ~1.33× box speed, so scorer CPU may be ~1.0–1.4× these values (HYPOTHESIS).
- The probe is not bit-for-bit deterministic in CPU; expect a few seconds of variation run to run. Simulated seconds and misses are deterministic.
- Re-run from this package (Nov 21, 9:4x AM ET): P2 printed `SIMULATED=119.933s misses=0 CPU(after warm imports)=16.94s`; the whole `eval_policy2.py` process (incl. Python start, imports, map load, forked probe children) measured `user 15.898s + sys 2.030s = 17.93 s` CPU, 17.5 s wall, via bash `time`. So on this box P2 ≈ 119.9 + 17.9 ≈ **137.9 s** with full-process CPU. The scorer's container CPU will still differ (Docker overhead, 2 CPUs, slower/faster cores).
- The P1 figure was 94.351 s simulated (= oracle) before the dialect map widened import-time candidates; current code gives 101.037 s.
- The official score is the **median of 3 runs**, wall limit 3 min (4.5 min final) on 2 CPUs.

## Rebuilding the maps (build-time; reconstructed commands — verify)
- Coverage contexts: run the full suite under coverage with per-test contexts and the syncpool plugin, e.g.
  `cd sqlglot && SKIP_INTEGRATION=0 coverage run --rcfile=../artifacts/coveragerc --context=test -m pytest -p syncpool -n 8 tests` with `plugins/` on `PYTHONPATH` and `dynamic_context = test_function` (or pytest-cov `--cov-context=test`). Then
  `python scripts/build_covmap.py <.coverage> kit/sample/tests.json artifacts/covmap_line.json`. Measured: ~80 s wall on 8 workers.
  Edit `data_file` in `artifacts/coveragerc` (it points at `/workspace/blast/cov/.coverage`).
- Dialect / fixture-read maps: run the suite with `-p dialectrec -p openrec` (set `BR_PLUG_OUT`), then merge the per-worker JSONs into `dialect_usage_map.json` / `file_reads_map.json` (the merge step was done ad hoc; the merged outputs are included).
- Import graph: `python scripts/import_graph.py ./sqlglot artifacts/import_graph.json`.
- All maps are built only from the pinned commit (allowed by the rules). Never ship anything learned from sample answers or mutants.

## Validation results (real)
- Sample (30 commits): numbers above. Caveat: the static hybrid was developed while looking at the sample, so 0 misses there is optimistic.
- Fresh held-out synthetic set (`results/recall_full2.jsonl`): 56 mutants, 45 broke something, **0/56 commit-level misses** for the static hybrid.
- Earlier set (`recall_full.jsonl`, 48 mutants): 2 misses on first pass (a `def` default argument; a dialect constant with no dialect-specific coverage), fixed generically (signature change ⇒ callers; runtime dialect map) ⇒ 0/48 after fixes.
- Probe vs labels: exact agreement on every probed test across all 30 sample commits.

## ⚠️ UNTESTED — must close before relying on this
1. **The 3 heaviest tests were excluded from the held-out ground truth**: `tests/test_parser.py::TestParser::test_max_nodes` (28.817 s, #1490), `tests/test_executor.py::TestExecutor::test_execute_tpcds` (21.454 s, #1106), `tests/test_generator.py::TestGenerator::test_generator_memory` (6.686 s, #1224) — `HEAVY` in `scripts/recall_full.py`. Recall on those tests in held-out data is unknown. The policy includes them only on evidence that the change is live.
2. **Syntax-error patches** (file fails to import ⇒ every test importing it fails). Untested; the safe rule is "patched module fails `compile()` ⇒ all tests that import it (transitively)".
3. **Edits to `tests/*.py` files** (test code changes, new/removed tests, conftest). Untested.
4. **New or deleted files, renames, binary diffs**. Untested; patch parser robustness unknown.
5. **Import errors / circular imports / changed `__init__` exports**. Untested.
6. **Perf-sensitive tests** (`test_max_nodes`, generator memory) triggered by slow-but-correct changes. Untested.
7. **Infinite loops / timeouts**: pytest-timeout value is unpublished; the probe needs its own timeout ⇒ treat as fail.
8. **Comment-only edits**: AST-equality ⇒ ∅ was right on the sample, but the organizers say comments aren't promised harmless, and comments inside SQL fixtures are *not* harmless. Only apply AST-equal ⇒ ∅ to `.py` files.
9. **Full container CPU** (Python start, imports, map load) and the 3-min wall limit on 2 CPUs: not measured in Docker yet.
10. `probe.py` is a prototype: it forks with an in-memory overlay. Its fidelity against real `pytest` on the patched tree hasn't been checked beyond the sample (experiment E4).

## Next steps (from the report)
1. Ship the static hybrid with committed maps + full-suite fallback (36.8 s/commit on 4 workers).
2. Add the P1 probe (≤2 s candidates, class-grouped, timeouts, crash ⇒ fail).
3. Docker + budget guard + robust patch parser + `make sample`; submit early (ties within 1 s go to earliest submission).
4. Cut probe CPU (P2, `__code__` hot-swap).
5. Close the untested cases above, then re-run a fresh held-out set.
