# Blast Radius opt2 progress (started 2026-11-21 10:45 ET)

Cmd base: `cd /workspace/blast && . venv/bin/activate && time BR_IDXCACHE=opt2/idx_cache.pkl python opt2/<eval>.py kit sqlglot artifacts 2.0 1.0 > out.jsonl`
Oracle on 30 sample commits: 94.351 s. CPU numbers = bash `time` user+sys of whole run (noise ±1 s).

| variant | simulated | misses | CPU (u+s) | total | /commit | mutant misses |
|---|---|---|---|---|---|---|
| P5 (opt, no idx cache) | 110.836 | 0 | 23.4–25.0 | ~135 | 4.5 | 0/164 (prev run) |
| P5 + BR_IDXCACHE (static index pickled at build) | 110.836 | 0 | 23.4 | 134.2 | 4.47 | same code path |
| P6 = P5 + preset expensive setUpClass in parent (TestExecutor 1.1 s/probe), duckdb record/replay conn | 110.836 | 0 | 17.8 | 128.6 | 4.29 | TBD |

Notes
- Simulated gap to oracle is exactly two commits: 4d63 (import-time dict value change in Snowflake tokenizer, 357 cands, include-all 9.5 s) and 6ze8 (6.686 s big test included unprobed, +7.0 s).
- Static phase was rebuilding the importtime index each run (2.4 s CPU); BR_IDXCACHE pickle (build-time, pinned commit only) cuts it to ~0.6 s.
- TestExecutor.setUpClass costs ~1.1 s CPU and ran in every forked probe. Now run once in parent; child reuses unless patch touched code it executed / files it opened (monitored). duckdb conns are parked (closed) before fork and replayed lazily in child (fork + duckdb threads deadlocked otherwise).
| P6 + partial import probe (reverse import closure from warm parent; full probe if closure hits `sqlglot` root) | 110.836 | 0 | 16.9 | 127.7 | 4.26 | 1 miss in full3 (i9_0025) — pre-existing static miss, see below |
| P7 = P6 + lambda-body rule + BR_IMPDERIV=1 | 103.851 | 0 | 16.7–17.1 (3 runs) | ~120.8 | 4.03 | 0/194 (full 48, full2 56, full3 60, lambda 30) |
| P8 = P7 + build-time pickled setUpClass state (opt2/preset_cache.pkl via build_presets.py) + per-run AST memo (astcache.py) | 103.851 | 0 | 15.2–15.5 | ~119.2 | 3.97 | 0/194 |

- FOUND: P5 code as left in opt/ actually misses mutant i9_0025 (opt/mutants_full3): Analyze.arg_types "kind" False->True; consumer is core.py:110 `required_args` derived in __init_subclass__ at import. The earlier 0/164 log predates the last opt/ edits. Fix: BR_IMPDERIV=1 (follow names derived at import) — static recall then 0 misses on all sets except i9_0019 (caught by import probe).
- Lambda-body rule (eval_baselines.lambda_only): touched line strictly inside a multi-line lambda that is a dict value/container element/plain assignment, patch changes nothing outside def/lambda bodies, touched line itself has coverage and that coverage is not a subset of the statement's pure import-time lines -> candidates = coverage of the lambda body lines only (no import-time fan-out). 6ze8: 37 cands -> 1. Validated on new set opt2/mutants_lambda (30 lambda-body mutants, seed 7, truth by full-suite probe): 0 static misses.
- fastprobe: lambda bodies stripped in plan_swap so lambda edits hot-swap; ambiguity guard for same-qualname same-line code objects.
| P9 = P8 + build-time per-test probe-CPU map (test_cpu.json via build_costs.py) used for EV, ordering, cheap/big split | 103.851 | 0 | 18.2–18.9 | ~122.5 | worse (split by cost probed more; probe-rest decisions too eager) | — |
| P10 = P8 + cost map for EV/ordering only (cheap/big split stays nominal) + heavy-but-cheap-to-probe tests probed only in live path + LIVE_P=0.5 prior for probing the rest of a live commit + P_FIX=0.02 + OVH=0.01 | 103.851 | 0 | 12.7–13.3 | ~116.9 | 3.90 | 0/194 |

- test_cpu.json: real in-process CPU per test at the pinned commit (fork + unittest, presets applied). Several "heavy" tests are cheap in-process because their nominal time is ProcessPool(spawn) startup: test_generator_memory 6.686 nominal -> 0.39 s, test_execute_tpch 3.863 -> 0.10, test_agg_order 1.364 -> 0.02. test_execute_tpcds (24.8) and test_max_nodes (21.7) stay expensive. build_costs.py takes ~40 s on 4 cores (fits the 10-min build) or the 25 KB json can be committed.
- preset_cache.pkl is 20 MB -> must be built in the Dockerfile (build_presets.py, ~2 s), not committed (repo limit 20 MB). chmod a+r.
| P11 = P10 + gc.disable() in parent (children re-enable) + build-time AST index (astindex.pkl: old-file AST hashes + lambda index) | 103.851 | 0 | see final table | | | running |
| P11 (measured 3 runs, resumed run) | 103.851 | 0 | 13.6 / 13.5 / 12.6 | ~117.1 | 3.90 | 0/194 (p11_mut1/2/3/L logs) — no real gain over P10 |
