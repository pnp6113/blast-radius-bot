# Scoring

`SCORING.md` and `score.py` are the authority. This note is the working picture of that formula on the sample catalog.

## One commit

Durations are whole milliseconds: `floor(seconds * 1000 + 0.5)`.

- No occupied workers: 0. Empty workers are dropped, so `[[1], []]` is one worker.
- Otherwise: the busiest worker's summed durations, plus 2000 ms for every occupied worker, at most 8.
- If any failing test number is absent: add 300000 ms. The worker cost is still charged.

A run's simulated time is the sum of those commit costs. The submission score is simulated time plus container CPU time (user plus system, every process, build time excluded). The image runs 3 times. The middle total is kept, rounded to 0.1 s with halves upward: `floor((microseconds + 50000) / 100000) / 10`.

`make sample` and `tools/docker_sample.py` print simulated seconds only. The board adds CPU. In-process `local_cpu_seconds` in the experiment log is this machine's process time, not the server's.

## Catalog shape

1620 numbered tests, 58 files, 103.047 recorded seconds.

Two tests set the floor for "run everything":

| Number | Seconds | File |
| --- | --- | --- |
| 1490 | 28.817 | `tests/test_parser.py` |
| 1106 | 21.454 | `tests/test_executor.py` |
| 1224 | 6.686 | `tests/test_generator.py` |

With every test selected, four workers is the best count on this catalog: the 28.817 s test is alone on its worker, the rest fits under that, and 8 seconds of overhead beats both 3 workers (the other two workers sit near 37 s) and 8 workers (16 s of overhead). Measured simulated time: 1104.510 s for 4 workers, 1210.470 s for the starter's 3 workers, 1344.510 s for 8 workers. All three miss nothing.

`auto` reproduces that 4-worker result when every test is selected, and it uses fewer workers when a selection is small. On the import-map selection that drops the sample from 1210.470 s to 482.337 s.

## What a miss costs

One missed commit adds 300 s on top of whatever workers you did run. Five misses is 1500 s, which is worse than running every test. Strategy `module` does that on this sample (1775.130 s, 5 misses). A selector that is unsure must run more tests, not fewer.

Running nothing is correct only when the patch fails nothing. Twelve of the 30 sample commits have an empty failing list: `2haz`, `2qn5`, `4d63`, `5y72`, `66ks`, `8y7j`, `9u5k`, `bap8`, `f4xa`, `fryt`, `gbav`, `ktz3`. Three of those are kind `comment`. The rules say a comment is not promised to be harmless, so the default selector still runs the mapped tests for those files.

## Output rules that reject a run

Stdout is UTF-8 JSON lines, one object per commit, keys exactly `commit` and `workers`. No BOM. No second copy of a commit. Test numbers are integers from `tests.json`, with no duplicates. More than 8 occupied workers, non-UTF-8, or more than 2 MB of stdout rejects the run. Logs go to stderr. The codes are in `SCORING.md`.

## Server limits

Build: network, 4 cores, 8 GB, 10 minutes. Downloads only from public package registries, fixed GitHub release tags, and sqlglot at `eb11aec`.

Run: no network, 2 cores, 4 GB, 3 minutes for a practice batch, 4.5 minutes for the final (about 1.5 times as many commits). User nobody, `HOME=/tmp`, read-only disk, 64 MB empty `/tmp`, 256 processes. Repository at most 20 MB. The committed map is about 670 KB.

The current selector plans the 30 sample commits in about 0.15 s of CPU. The final batch at that rate stays far inside 4.5 minutes. Running sqlglot's tests while deciding is allowed and is charged as CPU. Doing that inside the run, rather than baking a map at build time, will spend the time budget.
