# Ethan P10 — Blast Radius selector research code (branch `ethan/p10`)

**Status: research harness, NOT yet a submission entrypoint.** Nothing here was submitted. main is untouched.
Claude: integrate this into the submission selector (`/data/commits/*.patch` + `/data/tests.json` -> JSONL on stdout), Dockerfile, and budget guard.

## Best verified result (30 public sample commits, local scorer arithmetic)
P10: simulated 103.851 s, **0 misses**, whole-process CPU 12.7–13.3 s (bash `time` user+sys) => ~116.9 s total, 3.90 s/commit.
Held-out synthetic mutants: 0/194 commit-level misses (sets full 48, full2 56, full3 60, lambda 30). Oracle on sample: 94.351 s.
Full variant table and notes: `PROGRESS.md`.

## Reproduce (box layout: sqlglot checkout at pinned commit, venv from kit/requirements.lock + env-pip-freeze.txt)
```bash
# build-time (free in the official scorer; must run in Dockerfile — do NOT commit the pickles, preset_cache.pkl is ~20 MB)
python code/build_presets.py ...     # -> preset_cache.pkl (pickled expensive setUpClass state), ~2 s
python code/build_costs.py ...       # -> test_cpu.json (included), ~40 s on 4 cores
# static index cache: run once with BR_IDXCACHE=<path> to create idx_cache.pkl (~5 MB)
# run
gunzip -k maps/covmap_line.json.gz
BR_IDXCACHE=code/idx_cache.pkl BR_ARTIFACTS=maps BR_IMPDERIV=1 time python code/eval_p10.py kit <sqlglot_dir> maps 2.0 1.0
```
Check each script's header/argv for exact arguments; paths were developed under /workspace/blast (opt2 dir held code + json maps + pickles side by side). Some helper scripts (heavy_check.py, static_recall.py) still hard-code /workspace/blast paths.

## Key mechanisms
static hybrid (statement coverage + import-time name index + BR_IMPDERIV derived-name following + class/dialect filters + fixture reads + lambda-body rule + AST-equal => empty for .py)
-> warm-parent fork probe with __code__ hot-swap (fastprobe.py), presets applied, duckdb conns parked before fork
-> EV rule using measured per-test CPU (test_cpu.json) to decide probe vs include; heavy tests (test_max_nodes, test_execute_tpcds) only on live evidence.

## UNTESTED / risks
- Syntax-error patches, tests/*.py edits, new/deleted/renamed files, import errors, perf-only changes, timeouts inside probes.
- Comments inside SQL fixtures are not harmless (only apply AST-equal => empty to .py).
- Full container CPU in Docker (2 CPUs, user nobody, read-only FS, 64 MB /tmp) not measured; preset_cache must be readable by nobody.
- Fork + duckdb threads previously deadlocked; handled by parking conns, verify in the real container.
- P11 (gc.disable + astindex.pkl) was still being measured when this branch was cut.

- `validation/` holds synthetic-mutant answers and recall logs for LOCAL validation only. Never ship them or derive tables from them (rule: no patch->tests lookup tables).
