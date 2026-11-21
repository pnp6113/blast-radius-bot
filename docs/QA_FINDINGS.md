# QA findings

Branch `feat/cursor-validation`, based on `2aeeb0b`. This branch does not change the selector, the map, or the scheduler. The numbers below are the foundation default: strategy `imports`, scheduler `auto`. They are sample-set measurements. The final batch is unseen and about 1.5× larger.

Reproduce:

```text
python -m unittest discover -s tests -v
python tools/qa_report.py spend
python tools/qa_report.py expansion
python tools/qa_report.py generalize
python tools/qa_report.py gaps
python tools/qa_report.py bench
```

`bench` runs the contest image twice under the contest flags. Machine-readable copies are in `experiments/qa/`.

## Where the 482.337 s goes

Official simulated time is 482337 ms, 0 misses. The split is exact:

| Piece | ms | Share |
| --- | ---: | ---: |
| Makespan (slowest worker on each commit) | 346337 | 71.8% |
| Worker overhead, 2 s × occupied workers | 136000 | 28.2% |
| Miss penalty | 0 | 0 |

Eight commits select at least 1600 of 1620 tests and cost 294536 ms together. Each one is the same shape: 4 workers, makespan 28817 ms, overhead 8000 ms, total 36817 ms. The makespan is one test, `tests/test_parser.py::TestParser::test_max_nodes`. Those eight commits are `2haz`, `2jrq`, `3utz`, `535z`, `5y72`, `bap8`, `ktz3`, `rszg`.

The other 22 commits cost 187801 ms. Thirteen of them are paced by `tests/test_generator.py::TestGenerator::test_generator_memory` (6686 ms) and cost 144447 ms together. `66ks` is paced by `tests/test_executor.py::TestExecutor::test_execute_tpcds` (21454 ms) and costs 25454 ms. The eight fixture commits cost 17900 ms, and on each of them the overhead is 2000 ms of a ~2–3 s commit.

`auto` never chose more than 4 workers on this sample: 12 commits use 1, 6 use 2, 4 use 3, 8 use 4. Slack on the non-critical workers sums to 119557 ms. Slack is not charged. The score already uses the slowest worker.

`test_max_nodes` failed on no sample commit. It was selected 8 times and it was the critical test all 8 times. `test_generator_memory` failed on no sample commit. It was selected 21 times and it was the critical test 13 times. Removing either test from the map would lower this sample score. That is not evidence the final batch can skip them.

## Widest dependency expansions

Shared modules map to 1617 tests: `sqlglot/helper.py`, `sqlglot/parser.py`, `sqlglot/tokens.py`, `sqlglot/dialects/dialect.py`, `sqlglot/expressions/__init__.py`, `sqlglot/optimizer/__init__.py`, `sqlglot/optimizer/optimizer.py`, `sqlglot/optimizer/scope.py`. The three tests outside that set are the `tests/test_sqlglotc.py` build tests. A one-line edit in any of those shared modules still pays 36817 ms on this catalog.

Some dialect modules are in that same set even though a dialect edit should be narrow. Mapped test counts:

| Module | Mapped tests |
| --- | ---: |
| `sqlglot/dialects/mysql.py` | 1619 |
| `sqlglot/dialects/athena.py` | 1617 |
| `sqlglot/dialects/hive.py` | 1617 |
| `sqlglot/dialects/presto.py` | 1617 |
| `sqlglot/dialects/trino.py` | 1617 |
| `sqlglot/dialects/tsql.py` | 1617 |
| `sqlglot/dialects/duckdb.py` | 1112 |
| `sqlglot/dialects/snowflake.py` | 360 |
| `sqlglot/dialects/bigquery.py` | 294 |
| `sqlglot/dialects/clickhouse.py` | 149 |

The bridges that pull the wide dialects into the core closure, from `experiments/qa/gaps.json`:

- `sqlglot/generator.py` imports `sqlglot.dialects.tsql` inside a function. `generator.py` is on the 1617-test path, so a TSQL edit selects `test_max_nodes`. Sample commit `535z` is that case.
- `sqlglot/optimizer/pushdown_predicates.py` imports `sqlglot.dialects.athena` and `sqlglot.dialects.presto` inside functions. The map treats that as a file-level edge.
- From there the chain is module-level: `athena.py` imports Trino and Hive, `trino.py` imports Presto, `presto.py` imports MySQL.

A function-level import is being charged to every test that imports the file. That is the expansion to cut. Cutting it by deleting the edge entirely would miss a test that actually calls that function. Coverage, or an edge that starts at the function, is the safe version.

## False negatives the map still has

These are import-breaking or behavior the test really executes. They are not sample-answer lookups.

1. `tests/test_typing.py::TestTyping::test_no_redundant_expression_metadata` (number 1619) walks `sqlglot.typing` with `pkgutil.iter_modules` and `importlib.import_module(f"sqlglot.typing.{info.name}")`. The map records string constants, so this test is missing from all 10 typing modules (`bigquery`, `clickhouse`, `databricks`, `duckdb`, `postgres`, `redshift`, `snowflake`, `spark`, `spark2`, `sqlite`). A redundant metadata entry, or an import error, fails this test and the current selector skips it.

2. `tests/test_dialect_imports.py::TestDialectImports::test_dialect_attribute_access_is_cached` (number 1053) loads `sqlglot.dialects.Snowflake` by attribute. `sqlglot/dialects/__init__.py` resolves that in `__getattr__`. The test is not in the 360 tests mapped from `sqlglot/dialects/snowflake.py`.

3. Module-level lazy imports fail collection of the whole test file, which the scorer counts as a failure of every test in the file. The map misses tests that do not otherwise name the dialect:
   - `tests/test_tokens.py` imports `BigQuery` at module level: 11 tests missing from `sqlglot/dialects/bigquery.py`.
   - `tests/dialects/test_dialect.py` imports `BigQuery`, `Snowflake`, and `Spark2` at module level: 52, 58, and 91 tests missing from those dialect modules.

   This bite is an import error. A behavioral edit that still imports can leave `test_agg` passing. Do not "fix" it by selecting every test in `test_dialect.py` for every dialect; Hive is already in the 1617-test closure, and BigQuery is 294 tests for the tests that actually name it.

Checked, and holding:

- `for name in Dialects` is attached. `test_generator_memory` is in the clickhouse dialect map.
- A fixture named by a string constant stays narrow. `tests/fixtures/optimizer/pushdown_predicates.sql` selects only tests whose id contains that stem.
- An unknown path, alone or beside a known file, falls back to all 1620 tests.
- A comment-only diff of `sqlglot/dialects/clickhouse.py` still selects the mapped tests. The comment heuristic does not shrink the set.
- The Athena deadlock test is selected only because `athena.py` sits in the 1617-test closure, not because the lazy `from sqlglot.dialects import Athena` was modeled. Snowflake, which is not in that closure, shows the same pattern as a miss.

## What does not generalize

Two answer-using trims were scored and must not be shipped.

Running only the tests that failed on that same commit, then rescheduling with `auto`, scores 94351 ms. The 387986 ms gap to 482337 ms is the headroom a correct narrower map could take. The oracle reads `answers.json`.

The other trim drops, on each commit, every selected test that failed on no other sample commit. Leave-one-commit-out misses 6 commits and scores 2101780 ms. The misses are exactly the commits whose failing tests appear nowhere else: `2jrq` (3 tests), `3utz` (22), `6vvp` (1), `6ze8` (1), `ary6` (5), `bj7g` (1). Six misses × 300 s is 1800 s. Remembering the sample's failing tests does not survive holding out a single commit.

The current selector's own misses on this sample are zero in every kind: code 347444 ms, multi 58804 ms, comment 58189 ms, fixture 17900 ms.

## CPU and Docker

In-process `plan_batch` median over 5 runs: 0.0625 s. That is this Windows interpreter, not the board.

Contest-flag container, image `blast-radius-entry`, user 65534, read-only, no network, 2 CPUs, 4 GB, 64 MB `/tmp`:

| Measurement | Value |
| --- | --- |
| `time.process_time` inside the container | 0.194414 s |
| `ru_utime` + `ru_stime` | 0.2512 + 0.07065 = 0.32185 s |
| `ru_maxrss` | 23268 KB |
| Two stdout SHA-256 | `8705fbd5bbf55fdd781386ab5a9c7f323797973b46479d23302137420f4c8fec`, identical |
| Two official scores | 482337 ms, 0 misses, identical |

Container CPU on this run is about 0.3 s. It does not explain the practice board's 1614.1 s starter line against the local starter's 1210.470 s simulated. RSS is far under 4 GB. This branch does not change `Dockerfile`, `blast/`, `selector.py`, or `maps/`, and those paths are what the image copies.

## What to change next, and where

1. Stop charging a nested import to every test of the file. The first edges are `generator.py` → `dialects/tsql.py` and `pushdown_predicates.py` → `dialects/athena.py` and `dialects/presto.py`. Do this in `tools/build_import_map.py`, then rebuild `maps/dependencies.json` and rerun `python tools/qa_report.py spend`. The win is real only while the false-negative tests below stay selected.
2. Record `pkgutil` walks that `import_module` an f-string prefix. Test 1619 has to be selected for every `sqlglot/typing/*.py`.
3. Record module-level `from sqlglot.dialects import Name` and attribute loads of `sqlglot.dialects.Name` as edges to `sqlglot/dialects/<name>.py`. Test 1053 has to follow Snowflake. Keep the fallback for an unmapped path.
4. Leave `auto` in place. On this sample it already refuses 5–8 workers. Overhead is 136 s, and the rest of the score is the two long tests the map keeps handing it.
5. Do not train on `sample/answers.json`. The leave-one-commit trim is the counterexample.
