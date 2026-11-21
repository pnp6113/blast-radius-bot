# Handoff for Claude

Do not submit to the leaderboard from this tree until you have decided the commit yourself. The practice board is not the prize. The cutoff is Sunday 11:00 AM Eastern.

## Where the work is

Local tree: `D:\Hackathon\blast-radius`.

GitHub was not created. `gh` is not on PATH, and `GH_TOKEN` / `GITHUB_TOKEN` are unset. Create `pnp6113/blast-radius-bot` when auth is available, public, Dockerfile at the root. Do not touch the Mech Royale repositories.

The official starter is preserved at `starter/selector.py`. Restore it without editing code:

```sh
BLAST_STRATEGY=all BLAST_SCHEDULER=starter BLAST_WORKERS=3 python selector.py
```

The image default is `imports` + `auto`. On the sample that is 482.337 simulated seconds, 0 misses, 0 fallbacks. The starter on the same scorer is 1210.470 s, 0 misses. The board's 1614.1 s starter line includes server CPU, which `make sample` does not print.

## Commands

```sh
python -m unittest discover -s tests -v
python tools/experiment.py compare
python tools/experiment.py diagnose --strategy imports --scheduler auto
python tools/docker_sample.py
```

`python tools/docker_sample.py` is the contest docker run (linux/amd64, no network, 2 CPUs, 4 GB, read-only, nobody, 64 MB `/tmp`). `make sample` is the same thing when `python3` and `make` exist.

## What is already true

- Stdout is one compact JSON line per commit. Logs are stderr JSON.
- A file missing from `maps/dependencies.json`, or a selection that throws, runs every test.
- The map was built from sqlglot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, not from `sample/answers.json`.
- `blast/` and `selector.py` do not read the answers. `tests/` and `tools/experiment.py` do.
- Four workers beats three and beats eight when every test is selected, because of the 28.817 s parser test. `auto` applies that tradeoff per commit.
- Strategy `module` misses 5 sample commits (`2jrq`, `3utz`, `535z`, `ary6`, `j6uz`) and must stay off.
- A seed-7 holdout of 9 commits gave `module` zero holdout misses and five train misses. Do not trust a small holdout alone.
- Planning the sample takes about 0.15 s. The limit that matters is simulated test time, then a miss penalty.

## Where to change the algorithm

Dependency quality: `tools/build_import_map.py`, then commit the regenerated `maps/dependencies.json` and re-run `compare`. The selector in `blast/select_imports.py` should stay a lookup plus a full-test fallback.

Scheduling: `blast/schedule.py`. `auto` is the piece to extend. Do not charge a made-up per-file startup; the scorer sums recorded test seconds only.

New strategies: a function that returns `(numbers, fallback, notes)`, a branch in `blast/engine.py`, a row in `tools/experiment.py` `compare`. Leave the default alone until that row has zero sample misses.

## Highest-priority next work

1. The import map is file-level. A small dialect edit still drags in every test of every file that imports that module, including the 6.686 s generator test on many commits. Split dependencies down to the test function that can actually execute the changed code.
2. A coverage map from one build-time pytest run of the pinned commit is allowed, and it is the credible way to approach the 130 s practice scores. Commit the map. Do not rerun the suite on every image build if you can avoid it; builds that run the suite take minutes and the final rebuilds every image.
3. Core modules (`dialect.py`, `serde.py`, `expressions`, widely imported optimizer modules) still select about 1617 of 1620 tests. Those commits dominate 482 s. Coverage or a proven safe subset is the gain. Guessing is a 300 s miss.
4. Fixture attribution is whole-test today. A fixture with one bigquery case selects the whole test, which may be a long optimizer test. Case-level attribution is the next cut, after coverage.
5. Before any submission, run `compare` and `docker_sample.py` on the commit you will enter. If misses are not zero on the sample, do not submit that commit.

## Risks

- 482.337 s is the sample. The final has edits this map has not seen. One miss is +300 s and the worker time you already spent.
- `dialect.py` loads dialects dynamically. The map does not connect every test to every dialect, on purpose. A test that loads a dialect without naming it and without looping over `Dialects` can be missed.
- Comment-only edits are not skipped. The rules refuse that shortcut.
- Do not hardcode sample patch ids, edit kinds, or failing test lists. Top entries are read.
- Repository limit 20 MB. The map is ~670 KB. Do not commit `.cache/sqlglot`.
- Image files must stay world-readable. `nobody` runs the container.
