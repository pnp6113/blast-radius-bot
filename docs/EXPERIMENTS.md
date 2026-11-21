# Experiments

All figures are the sample set: 30 patches, `score.py`, one local process. The final batch is unseen, about 1.5 times as large, and can contain edit kinds that are not here. A sample gain is not a leaderboard score. The published starter line on the board is 1614.1 s, which includes server CPU that this log does not.

Reproduce:

```sh
python tools/experiment.py compare
python tools/experiment.py schedule
python tools/experiment.py holdout --seed 7
python tools/experiment.py profile
```

`experiments/latest.json` is the `compare` output.

## Compare

| Strategy | Scheduler | Workers | Simulated | Misses | Fallbacks | Local CPU |
| --- | --- | --- | --- | --- | --- | --- |
| all | starter | 3 | 1210.470 s | 0 | 0 | 0.08 s |
| all | lpt_ms | 4 | 1104.510 s | 0 | 0 | 0.05 s |
| all | lpt_ms | 8 | 1344.510 s | 0 | 0 | 0.06 s |
| all | auto | 4 (chosen per commit) | 1104.510 s | 0 | 0 | 0.47 s |
| module | lpt_ms | 4 | 1775.130 s | 5 | 3 | 0.45 s |
| imports | lpt_ms | 4 | 532.808 s | 0 | 0 | 0.06 s |
| imports | auto | chosen per commit | 482.337 s | 0 | 0 | 0.14 s |

`imports` + `auto` is the default because this run missed nothing and every changed file was in the map. It is still a sample result.

## Schedule sweep

On "run every test", `starter` and `lpt_ms` tie. `round_robin` and `pack_files` are worse at every worker count that matters. Best run-all point: 4 workers, 1104.510 s. Eight workers adds 2 s of overhead thirty times (60 s per extra worker across the batch) and does not lower the makespan below the 28.817 s parser test.

## Module misses

Strategy `module` missed 5 commits. The other three odd rows fell back to every test and did not miss.

| Commit | What happened |
| --- | --- |
| `2jrq` | `eliminate_joins.py` matched 1 test and missed 7. The optimizer tests do not carry that filename. |
| `3utz` | `serde.py` matched 5 tests and missed 32. Serde is used outside `test_serde.py`. |
| `535z` | `dialects/tsql.py` matched 65 tests and missed 1. |
| `ary6` | `parsers/bigquery.py` matched 72 and missed 6, including optimizer tests. |
| `j6uz` | Same parser, missed `test_annotate_funcs`. |
| `66ks`, `nwnx`, `rszg` | Fallback to every test. |

Do not turn `module` on. The import map exists because name matching is not a dependency graph.

## Holdout

Seed 7, 9 of 30 commits held out. Nothing was fitted on either side.

| Strategy | Train simulated | Train misses | Holdout simulated | Holdout misses |
| --- | --- | --- | --- | --- |
| all / starter | 847.329 s | 0 | 363.141 s | 0 |
| module / lpt_ms x4 | 1690.998 s | 5 | 84.132 s | 0 |
| imports / auto | 298.496 s | 0 | 183.841 s | 0 |

The module holdout missed nothing while the train side missed all 5. A 9-commit holdout can hide a bad selector. Use it as a second look, not as proof.

## Profile

`imports` over 30 commits: 0.148 s, about 83k calls. The largest pieces are decoding `tests.json` and the map, then the longest-first scan (`min` on worker loads). That is not the time budget. The time budget is the simulated test seconds.

## How to read a later result

- `misses > 0` on the sample is a bug in that strategy. Fix the map or fall back.
- A lower simulated time with 0 sample misses is a candidate. It is not a final score.
- If train and holdout disagree on misses, the strategy is brittle. Do not ship it on the strength of the split that looks clean.
