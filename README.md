# Blast Radius

Test selection for [sqlglot](https://github.com/tobymao/sqlglot) at commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. Lower simulated CI time wins. Missing a failing test costs 300 seconds. The rules are in [SCORING.md](SCORING.md). The original kit text is in [starter/README.md](starter/README.md).

This repository is a foundation for later algorithm work. It has not been submitted to the leaderboard.

## What runs

`selector.py` prints one JSON line per patch. The default, chosen after the sample comparison below, is:

- strategy `imports`: tests recorded in `maps/dependencies.json` for each changed file
- scheduler `auto`: 1 to 8 workers, whichever has the lower makespan-plus-overhead

An unmapped file selects every test. Set the switches back to the official starter with:

```sh
BLAST_STRATEGY=all BLAST_SCHEDULER=starter BLAST_WORKERS=3
```

That path calls the unmodified selector in `starter/selector.py`.

## Sample scores

These are sample-set numbers from `score.py`. They are not a leaderboard score. The board also adds container CPU time and takes the middle of 3 runs. The published starter line is 1614.1 s.

| Plan | Simulated | Misses |
| --- | --- | --- |
| Official starter, every test, 3 workers | 1210.470 s | 0 |
| Every test, 4 workers | 1104.510 s | 0 |
| Import map, automatic worker count (default) | 482.337 s | 0 |

## Commands

Python 3.9 or newer. Docker is required for the contest path. `make` is optional; on Windows use the Python scripts.

```sh
python -m unittest discover -s tests -v
python tools/experiment.py compare
python tools/experiment.py diagnose --strategy imports --scheduler auto
python tools/experiment.py schedule
python tools/experiment.py profile
python tools/experiment.py holdout --seed 7
python tools/docker_sample.py
python tools/build_import_map.py
```

`make sample` and `make check` are the kit's original docker targets. `make test`, `make experiment`, `make schedule`, `make profile`, `make holdout`, and `make map` call the same Python commands.

## Layout

Handoff notes for the next agents are in [docs/CLAUDE_HANDOFF.md](docs/CLAUDE_HANDOFF.md) and [docs/CODEX_HANDOFF.md](docs/CODEX_HANDOFF.md).
