# Architecture

## Layout

| Path | Role |
| --- | --- |
| `selector.py` | Contest entry. Reads `BLAST_STRATEGY`, `BLAST_SCHEDULER`, `BLAST_WORKERS`, `BLAST_DATA`. Stdout is answer lines only. |
| `starter/selector.py` | Official starter, unchanged. Every test, 3 workers, longest-first in float seconds. |
| `blast/engine.py` | `plan_batch` turns a data directory into one plan per commit. A selection error falls back to every test. An unknown strategy name still raises. |
| `blast/patch.py` | Unified-diff paths and a comment-only hint that must not gate selection. |
| `blast/load.py` | `tests.json`. Durations use the same half-up millisecond rounding as `score.py`. |
| `blast/schedule.py` | `starter`, `lpt_ms`, `round_robin`, `pack_files`, `auto`. At most 8 occupied workers. |
| `blast/select_module.py` | Strategy `module`. Name match, else every test. |
| `blast/select_imports.py` | Strategy `imports`. `maps/dependencies.json`, else every test. |
| `maps/dependencies.json` | File to test numbers, built from sqlglot `eb11aec`. Not built from `answers.json`. |
| `tools/build_import_map.py` | Rebuilds that map from `.cache/sqlglot` or a path argument. |
| `tools/experiment.py` | Scores plans with the official `score.py`. |
| `tools/docker_sample.py` | `make sample` for a host without `python3` on `PATH`. |
| `score.py`, `SCORING.md`, `sample/`, `requirements.lock` | Official kit. Do not fork the scoring rules. |
| `Dockerfile` | Pinned `python:3.13-slim` digest from the kit. Copies `starter`, `blast`, `maps`, `selector.py`. |

`sample/answers.json` is mounted nowhere in the image. Runtime code under `blast/` and `selector.py` does not open it. Tests and `tools/experiment.py` do, so they can explain misses.

## Interfaces

`plan_batch(data_dir, strategy, scheduler, worker_count) -> list[CommitPlan]`

`CommitPlan` has `commit`, `workers`, `selected`, `fallback`, `notes`, `comment_only`.

A strategy function returns `(test_numbers, used_fallback, notes)`. Add one in `blast/` and one branch in `blast/engine.py:_select`. Keep the new name off the default until `tools/experiment.py compare` has been run.

`assign(numbers, tests_by_number, durations_ms, worker_count, method) -> list[list[int]]`

`auto` ignores `worker_count` and picks 1..8 by the scorer's own formula: largest worker load in milliseconds, plus 2000 ms per occupied worker.

## Branches

Work on the selector belongs on a branch that does not rewrite `score.py` or the sample set.

- Selection and the map: `blast/select_*.py`, `tools/build_import_map.py`, `maps/dependencies.json`.
- Scheduling: `blast/schedule.py` only.
- Harness and reports: `tools/experiment.py`, `tests/`, `docs/EXPERIMENTS.md`.

The default image behavior is `imports` plus `auto`. Restore the starter without a code change:

```sh
BLAST_STRATEGY=all BLAST_SCHEDULER=starter BLAST_WORKERS=3
```

## Data the program sees

`/data/tests.json` is `{"tests": [...], "skipped": [...]}`. Each test is `number`, `id`, `file`, `seconds`. Numbers are `0..1619` in this catalog (1620 tests).

`/data/commits/<id>.patch` is one unified diff against the pinned commit. The id is the filename without `.patch`. Patches do not stack.

## Adding dependency information

`tools/build_import_map.py` reads the pinned checkout and writes `maps/dependencies.json`:

```json
{"source_commit": "eb11aec4be8cb2122afe713a4816662c3f0fb2fa", "files": {"sqlglot/parsers/bigquery.py": [1, 2]}}
```

Edges it records today:

- Static imports, skipping `TYPE_CHECKING` blocks, including parent packages' `__init__.py`.
- Lazy `sqlglot.*` strings in files that mention `importlib` or `_LAZY_ATTRS`.
- Fixture paths and stems named by a test function, plus class attributes such as `dialect = "bigquery"`.
- `# dialect: bigquery` inside a fixture, attached to the tests that load that fixture.
- A test that loops over `Dialects` depends on every dialect, parser, generator, and typing module.

It does not treat `dialect.py`'s dynamic loader as "every test depends on every dialect". That edge selects every test for every dialect edit.

Rebuild after changing the builder:

```sh
python tools/build_import_map.py
python -m unittest discover -s tests -v
python tools/experiment.py compare
```

The checkout used to build the committed map is `.cache/sqlglot` at `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. `.cache/` is gitignored. The contest build must not need that checkout; the map is already a file in the image.
