# Handoff for Codex

The selector already decides a set of tests. Your side is the cost model and the assignment of those tests to workers, plus any tighter selection that still goes through the same interfaces.

## Cost, in the code

`score.py` is the scorer. Do not reimplement it for official totals. `blast/load.py:duration_ms` matches its half-up rule. `blast/schedule.py:_predicted_ms` is the commit cost the `auto` scheduler minimizes:

```
max(worker loads in ms) + 2000 * occupied_workers
```

No workers means 0. More than 8 is rejected, and `assign` caps at 8. Empty workers are removed before the cost, matching the scorer.

The recorded catalog sum is 103.047 s. Two tests dominate a full run: number 1490 at 28.817 s (`tests/test_parser.py`) and number 1106 at 21.454 s (`tests/test_executor.py`). A third, number 1224 at 6.686 s (`tests/test_generator.py`), is what makes many dialect selections cost about 14.7 s under a fixed 4 workers (6.686 s plus 8 s of overhead). `auto` drops those commits onto fewer workers. That is why `imports` moved from 532.808 s at 4 workers to 482.337 s with `auto`, still with 0 sample misses.

Measured full-catalog sweep, 0 misses every row:

| Workers | starter / lpt_ms | round_robin | pack_files |
| --- | --- | --- | --- |
| 3 | 1210.470 s | 1546.620 s | 1210.470 s |
| 4 | 1104.510 s | 1461.420 s | 1186.950 s |
| 8 | 1344.510 s | 1482.240 s | 1426.950 s |

`pack_files` keeps a file's tests together. The scorer does not charge pytest startup, so that constraint only hurts. `round_robin` does not put the 28.817 s test on a worker by itself in a useful way. Leave both in the sweep. Do not make either the default.

## Where to edit

- New scheduler: a function in `blast/schedule.py` and a name in `assign`. Add the name to the choices in `tools/experiment.py`.
- Prove it: `python tools/experiment.py schedule` and `python tools/experiment.py compare`. The compare harness throws if a hand-rolled per-commit sum disagrees with `score.evaluate_run`.
- Selection changes do not belong in the scheduler. A scheduler receives test numbers and durations. It must return each number at most once.

`auto` currently tries every worker count from 1 to 8 with longest-first placement. Ties keep the smaller count, because the loop replaces the best only on a strictly lower cost. That is the right tie break: fewer workers is never worse when the makespan is equal, and the loop sees smaller counts first.

## Selection facts that affect the schedule

The default selection is `maps/dependencies.json` via `blast/select_imports.py`. Core files still return ~1617 tests, so those commits schedule like a full run and want 4 workers. Dialect and fixture commits return a handful of tests plus, often, a long test from a file that imported the module. The next scheduling win on those commits is smaller than the next selection win: stop handing `auto` the 6.686 s and 28.817 s tests when the patch cannot fail them.

Strategy `module` is a negative example. It missed `2jrq`, `3utz`, `535z`, `ary6`, and `j6uz`. Five misses add 1500 s. Its sample total is 1775.130 s. Do not schedule your way out of a miss.

## Tests to add when you change assignment

`tests/test_foundation.py` already checks:

- starter placement matches `starter/selector.py` on the full catalog
- longest-first covers every number once and puts the heaviest test on the first worker it fills
- empty input returns no workers
- a request for 30 workers still returns at most 8
- `imports` + `auto` scores exactly 482337 ms with no missed sample commit

If you change `auto` and the 482337 ms assertion fails, that is the sample score moving. Update it only after `compare` shows 0 misses and you intend the new number. Do not weaken the miss assertion.

## Do not

- Read `sample/answers.json` from `blast/` or `selector.py`.
- Encode "this patch id runs these tests".
- Skip comment-only diffs. `blast/patch.py:comment_only_heuristic` is a report hint.
- Put logs on stdout. `blast/logutil.py` writes stderr.
- Commit `.cache/` or a sqlglot checkout. The image already has the map.
