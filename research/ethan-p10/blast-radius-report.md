# Blast Radius — Research & Implementation Blueprint (Team Last Mech Standing)

Prepared Sat Nov 21 2026 (ET). Everything below is either **VERIFIED** (with a source: URL, kit file + line, or a
measurement we actually ran on the box, with the artifact path) or labelled **HYPOTHESIS**. No benchmark number
in this document is invented; every measured number was produced by a script in `/workspace/blast/artifacts/scripts/`.

Workspace: `/workspace/blast/` — `kit/` (official starter kit, unpacked), `sqlglot/` (pinned checkout),
`artifacts/` (maps, timings, scripts, raw results), `mutants*/` (synthetic local-validation patches, not for shipping).

---

## 0. Crucial facts first (read this if nothing else)

1. **The practice set is 40 commits; the final is ~60.** VERIFIED arithmetically: the published baseline 1614.1 s
   ÷ the exact starter cost per commit (full suite LPT on 3 workers = 34.349 s + 3×2 s = 40.349 s) = 40.003 commits
   (the tiny residue is the starter's CPU time). Final has "about 1.5x as many commits" (track page).
2. **Board leaders are essentially at oracle level.** VERIFIED from the track page board (snapshot today): best 130.7 s,
   2nd 135.4 s, 3rd 138.1 s on 40 commits = **3.27 s/commit including CPU**. On the 30 public sample commits the
   perfect-knowledge oracle costs **94.351 s = 3.145 s/commit** (measured, `eval_baselines.py`/oracle calc).
   ⇒ The top teams have ~0 misses and spend only a few seconds of CPU per batch.
3. **One miss (300 s) is worth more than two leaders' entire practice scores of selection cost.** In the final
   (~60 commits, expected leader ≈ 190–200 s), *any* miss is almost certainly fatal for 1st place. The final contains
   "kinds of change not in practice" (track page). ⇒ Recall is the #1 objective; CPU+selection cost is #2.
4. **Full-suite fallback is cheap relative to a miss: 36.817 s/commit** (best split = 4 workers; the 28.817 s
   `test_max_nodes` bounds the makespan). VERIFIED by exact ms arithmetic over `tests.json`.
5. **CPU time of everything in the container is charged** (selector + any sqlglot tests we run while deciding);
   build is free. VERIFIED: SCORING.md l.51–53, track page.
6. **Runtime probing works and is exact on the sample.** Running candidate tests against the patched sqlglot
   in a forked child (in-memory overlay, no disk writes) reproduced the official failing set **exactly for every probed
   test on all 30 sample commits** (0 false positives, 0 false negatives). VERIFIED (`eval_probe.py`, runs logged above).
7. **Tie rule in the final:** scores within 1.0 s of the best are tied and ranked by *earliest submission*.
   VERIFIED (track page "Ties"). ⇒ Submit the final-candidate commit as early as possible; late marginal gains <1 s are worthless.
8. **Comment-only Python edits:** AST-equality correctly classified all 3 sample "comment" commits and the comment
   parts of the 3 "multi" commits as harmless; but the official warning stands ("not promised to be harmless") and
   **comments inside SQL fixtures are not harmless** (sqlglot parses and preserves SQL comments). Details §5.

---

## 1. Competition & scoring interpretation (VERIFIED unless marked)

Sources: track page https://www.joinbuilders.org/buildathon/tracks/blast-radius ; `kit/SCORING.md`; `kit/README.md`;
`kit/score.py`; `kit/Makefile`; `kit/Dockerfile`.

**Input.**
- `/data/commits/<id>.patch`: one unified diff against sqlglot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`; each patch
  stands alone (SCORING.md l.9, README). Sample patches are small (12–38 lines), `git diff` format with `a/`/`b/`
  prefixes and `index` lines, 1–3 files each.
- `/data/tests.json`: `{"tests":[{"number","id","file","seconds"}...], "skipped":[ids]}` (SCORING.md l.7).
  Test identity = **pytest node id at test-method granularity** (`tests/test_parser.py::TestParser::test_union`);
  numbered 0..N-1 in listed order. Sample: **1620 numbered tests, 1 skipped**
  (`tests/dialects/test_sqlite.py::TestSQLite::test_variadic_iif_execution`), **sum of seconds 103.047 s**.
  A test with subtests is one test; any failing subtest fails it (track page; README l.66).
- Answers (sample only): `{"commits":{id:{"failing":[...],"kind":...}}}`. Sample kinds: code 16, fixture 8, multi 3,
  comment 3; **11/30 commits break nothing** (9 code, 3 comment) — VERIFIED from `sample/answers.json`.
- The program never sees answers; only patches + tests.json are mounted (Makefile `RUN_FLAGS`).

**Output.** One JSON line per commit `{"commit": id, "workers": [[...],[...]]}`; ≤8 non-empty workers; no duplicate
test in a line; only known numbers; every commit answered; nothing else on stdout; ≤2 MB stdout; UTF-8, no BOM
(SCORING.md table l.20–28). Any violation rejects the whole run (score unchanged).

**Cost of one commit (ms arithmetic).** duration = round(seconds×1000) half-up (score.py `_duration_milliseconds`);
load(worker) = Σ durations; cost = 0 if no workers, else max load + 2000 ms × (#non-empty workers)
(SCORING.md l.36–42). **Miss**: any failing test absent from all workers ⇒ +300 000 ms; the commit's own cost still counts (l.46).

**Total.** simulated = Σ commit costs + 300 s × misses; total = simulated + CPU time (user+sys of *every* process in the
container, start→exit) (l.48–53). Image runs 3×, all must be valid, **median** total counts, rounded to 0.1 s (l.57).
Build is not charged.

**"Fails" means** pytest failure, error, timeout, or the file cannot be imported (track page). Labels were produced with
`SKIP_INTEGRATION=0` (executor tests active) on the pinned `python:3.13-slim` digest with `requirements.lock`
(README l.67–68). pytest-timeout is in the lock file (timeout value not published — HYPOTHESIS: infinite-loop mutants
are labelled failing via timeout).

**Limits.** Build: network, 4 cores, 8 GB, 10 min; may clone sqlglot at the pinned commit and run its tests.
Repo ≤20 MB, no submodules/LFS. Run: no network, 2 cores, 4 GB, **3 min wall for the whole batch (4.5 min final)**,
user `nobody`, HOME=/tmp, read-only disk, empty 64 MB /tmp, ≤256 processes (track page, README). Downloads only
from PyPI/npm/Docker Hub/GitHub releases at fixed tag/sqlglot at the pinned commit.

**Eligibility / rules that bind design.**
- *No lookup tables*: "no table from a patch, or from a kind of edit, to the tests it breaks. Maps built from the
  pinned commit (coverage, imports, fixtures) are allowed. Top entries are read." ⇒ coverage/import/fixture/dialect
  maps: OK. Generic analysis + runtime probing: OK. A table "mutation type X at line Y → tests Z" (e.g. built by
  running our own mutants) is **not OK** — we use our synthetic mutants *only* for local validation and for a couple of
  scalar policy thresholds (see §5), never shipped as a map. Hard-coded handlers for specific constants seen in
  practice (e.g. `WEEK_OFFSET`, `NUMERIC_ESCAPES`) are an overfitting + "kind of edit" rule risk — avoid.
- No code sharing between teams; identical repo/commit/archive ⇒ later one disqualified.
- Practice board carries no prize; only the final counts; at least the top 5 are re-run 5× on one idle machine.
- Rate limit: 1 submission/hour initially.

**Exact decision math (per commit).** Let S = selected set, k = workers, M(S,k) = LPT makespan.
`cost(S) = min_k [M(S,k) + 2k]` (0 if S=∅). `E[total] = cost(S) + 300·P(miss | S) + CPU`.
- Adding test t with marginal miss-probability reduction Δp is worth it iff `300·Δp > Δcost(t)`; Δcost is 0 when t fits
  under the current makespan on an existing worker, 2 s if it forces a new worker, or its extra makespan otherwise.
- **Zero-test answer** is right iff `300·P(any failure) < cost(S*)`, i.e. with a cheapest non-empty answer of ≈2.0–2.6 s,
  you need **P(any failure) < ~0.7–0.9 %**. Pure guessing cannot reach that; it must be proven (AST equality) or measured (probe).
- **Full-suite fallback (36.817 s)** beats a targeted guess S iff `300·P(miss|S) > 36.817 − cost(S)`, i.e. whenever
  P(miss|S) > ~12 % (for small S). Use it only when analysis genuinely fails (parse failure, unknown file kinds).
- **Expensive test t (28.8/21.5/6.7/3.9/3.5 s):** include iff P(t fails | evidence) > sec(t)/300 = 9.6 %/7.2 %/2.2 %/1.3 %/1.2 %.
- **Probe vs include:** probing test set P costs CPU(P) ≈ α·Σsec + overhead (measured α≈1.0–1.3, overhead ≈0.13–0.2 s/fork on the box);
  including it costs at most its contribution to makespan + worker charges. Probing a test never beats including it
  *for that test alone*; it pays off only by (a) turning "≥2 s" answers into ∅ when everything passes, (b) shrinking
  the set to the true failing subset, (c) avoiding expensive-test inclusion when evidence says the change is dead.

---

## 2. SQLGlot dependency structure (measured at eb11aec)

Pinned commit VERIFIED: `eb11aec4be8cb2122afe713a4816662c3f0fb2fa 2026-11-17 17:18:00 -0600 fix(sqlite): parse variadic IIF and IF calls`.
184 `.py` files in `sqlglot/` (88 223 lines), 66 in `tests/`; 3.5 MB source, 8.8 MB tests.

**Package layout.** Core: `tokenizer_core.py` (1 438 l), `tokens.py`, `parser.py` (11 017 l), `generator.py` (6 871 l),
`expressions/` (core.py 3 183 l, query.py, builders.py, functions, datatypes, ddl, dml, properties…),
`dialects/dialect.py` (3 119 l) + 34 dialect files, with **per-dialect split modules** `parsers/<d>.py`,
`generators/<d>.py`, `typing/<d>.py` (e.g. duckdb generator 5 134 l). `optimizer/` (scope, qualify_columns, simplify,
annotate_types, …), `executor/` (python executor), `transforms.py`, `schema.py`, `serde.py`, `lineage.py`, `diff.py`, `planner.py`.

**Static import graph** (`artifacts/import_graph.json`, script `import_graph.py`): `sqlglot/__init__` imports
dialect, diff, errors, expressions, generator, parser, schema, tokens ⇒ **58 of 60 test modules statically import
the whole core** (median closure 43 modules; 87 if lazy/function-level imports are included). Dialects are loaded lazily by
name at runtime. ⇒ File/import-level RTS (STARTS/Ekstazi style) degenerates to "almost everything" for core files.

**Per-test coverage fan-in** (`covmap_line.json`, 36 737 covered lines over 171 files; contexts = test node ids):
- line fan-in: median **7** tests, p90 137, p99 1 480, max 1 583; 7 672 lines covered by exactly one test.
- file-union fan-in (`fanin_per_file.json`): dialect.py 1 586, tokens.py 1 567, tokenizer_core.py 1 565,
  expressions/core.py 1 540, parser.py 1 519, `__init__` 1 518, helper.py 1 475, generator.py 1 349, query.py 973,
  transforms.py 782, scope.py 732 … ⇒ **line/statement granularity is mandatory**; file granularity selects most of the suite for hub files.

**Runtime distribution** (VERIFIED from tests.json; box timings in `test_timings.csv`):
total 103.047 s; by file: test_executor 31.6 s (60 tests), test_parser 29.1 s (81), test_optimizer 19.8 s (167),
test_generator 6.75 s (10), everything else ≈16 s across ~1 300 tests (dialect tests are mostly milliseconds).
Heavy tests: `test_max_nodes` 28.817, `test_execute_tpcds` 21.454, `test_generator_memory` 6.686, `test_execute_tpch` 3.863,
`test_optimized_tpch` 3.537, `test_scope` 2.533, optimizer `test_tpcds` 2.232, `test_annotate_funcs` 1.963 …
Box measurement: full suite 78.1 s wall serially (1620 passed, 1 skipped, 25 440 subtests), Σ measured per-test 77.1 s
⇒ the box is ~25 % faster than the recorded seconds.

**Gotchas that break naive coverage maps (all VERIFIED by experiment):**
1. **Process pools.** `TestOptimizer` (all fixture-driven `check_file` tests) and `TestExecutor._mp_execute`
   (tpch/tpcds) run work in `ProcessPoolExecutor(spawn)`. Plain `pytest-cov --cov-context=test` misses that work:
   line-level selection then missed 5/30 sample commits. Re-running coverage with an in-process synchronous executor that
   pickle-round-trips arguments/results (`scripts/syncpool.py`) fixed 3 of them (misses 5→2). The pickle round-trip
   matters: expression pickling goes through `serde` (sample 3utz: a serde mutant breaks 37 tests incl. optimizer).
2. **Multi-line statements & short-circuiting.** Coverage only records lines actually evaluated; a mutated 3rd clause of
   a multi-line `if (... and ... or ...)` may not be "covered" by a test that evaluated clause 1. Expanding each
   changed line to its enclosing statement header (`stmt` mode) fixed sample rszg (misses 2→1).
3. **Import-time code** (module/class bodies, dict tables, class constants, `def` default values, decorators) runs
   during collection with **no test context**. Sample 535z (`TSQL.WEEK_OFFSET = -1 → -2`) breaks
   `test_optimizer::test_simplify` only. Needs name-use analysis (§4 C3) — fixed misses 1→0 on the sample.
4. **Class-level setup** (`setUpClass` in TestExecutor/TestOptimizer/TestLineage/TestSQLGlotC) and shared caches
   (`TestExecutor.cache` keyed by SQL) are attributed only to the first test that triggered them; results can be
   order-dependent. Treat setup-phase coverage/reads as belonging to every test in the class.
5. **Dialect usage is often invisible in coverage.** e.g. `test_executor::test_typed_division` uses `dialect="postgres"`
   but executes no Postgres-specific line; a mutant of `Postgres.TYPED_DIVISION` broke it and our coverage-derived dialect
   filter missed it. Fixed with a runtime **dialect-instantiation map** (`dialect_usage_map.json`, `scripts/dialectrec.py`:
   records every `Dialect` subclass instantiated per test; 35 dialect classes, e.g. DuckDB 437 tests, Snowflake 358, Postgres 320, TSQL 171).
6. **Data files.** Fixture reads recorded with an audit hook (`file_reads_map.json`): each `tests/fixtures/optimizer/<x>.sql`
   is read by exactly one optimizer test (e.g. simplify.sql → #1434; qualify_columns_ddl.sql → #1382 `test_qualify_columns`);
   identity.sql → 2 tests; pretty.sql, partial.sql, jsonpath/cts.json → 1 each; TPC-H/DS csv data → executor class setup.
   Note `helpers._filter_comments` drops fixture lines starting with `--`, but meta lines `# key: value` and inline SQL
   comments are parsed.

---

## 3. Measured baselines on the 30 sample commits (real numbers, local scorer arithmetic)

Script `artifacts/scripts/eval_baselines.py` (static), `eval_policy.py` (P1), `eval_policy2.py` (P2), probe in `probe.py`.
CPU numbers are box CPU (scorer CPU unknown; recorded seconds are ~1.33× box speed, so scale CPU by ~1.0–1.4 — HYPOTHESIS).

| Strategy | Simulated s | Misses | CPU s (box) | ≈Total |
|---|---|---|---|---|
| Oracle (perfect failing sets, optimal split) | 94.351 | 0 | – | 94.4 |
| Full suite, best split (4 workers) | 1104.510 | 0 | ~0 | 1104.5 |
| File-level coverage (with process-pool fix) | 534.316 | 1 | – | – |
| Line-level coverage (with fix) | 753.879 | 2 | – | – |
| Statement-level (stmt header expansion) | 454.325 | 1 | – | – |
| **Static hybrid** (stmt + import-time names + class/dialect filters + fixture reads + AST-equal ⇒ ∅) | **174.818** | **0** | <1 | ~176 |
| **P1** = static hybrid → probe every candidate ≤2 s (grouped by class) → include >2 s candidates iff some probe failed | **101.037** | **0** | 48.3 | ~149 |
| **P2** = P1 with a cost-aware probe/no-probe rule (priors, stage-1 prefix, include-rest-when-cheap) | 119.933 | 0 | 16.3 | ~136 |

(Earlier P1 run, before the dialect map widened import-time candidates: 94.351 simulated = exactly oracle, CPU 49.3 s.)
Caveat: the static hybrid was developed while looking at the sample, so its 0 misses there is optimistic; held-out
numbers follow.

**Held-out local validation (synthetic mutants, never shipped).**
- `calibration_mutants.jsonl`: 237 random single-line in-function mutants (operator/constant/negation swaps on covered
  lines). Ground truth = probing all statement-level candidates. 176/237 killed; **61/237 (26 %) not killed by any covering
  test**; median 37.5 % of candidates fail. For tests >2 s: in 0 of 29 cases did an expensive test fail while all ≤2 s
  candidates passed (with a 1 s threshold there were 9 such cases, all fixture-monolith tests 1.0–1.96 s).
  P(expensive test fails | change is live & it is a candidate): max_nodes 1/5, tpcds 21/51, generator_memory 1/3,
  tpch 4/9, optimized_tpch 3/8, test_scope 5/19, optimizer test_tpcds 26/40; P(fail | dead) = 0 in every case — all far above break-even.
- `recall_full.jsonl`: 48 mutants (24 import-time constant mutants, 24 in-function) with ground truth from probing the
  **entire suite except the 3 heaviest tests**. First pass of the static hybrid: 2 commit-level misses (a `def` default
  argument; a dialect constant used without dialect-specific coverage). After two *generic* fixes (signature changes ⇒
  callers; runtime dialect map) ⇒ 0/48 misses (no longer held-out after the fix).
- `recall_full2.jsonl`: a fresh, untouched set of 56 mutants run after the fixes — see §9 table (filled in at the end of this report).

---

## 4. Top-10 algorithm designs (opportunities), ranked by expected value for the final

Expected-impact statements are reasoned from the measurements above, not promised.

### A1. Statement-level per-test coverage map with process-pool capture (foundation) — **VERIFIED necessary**
- *What:* line→tests map from coverage.py dynamic contexts, collected with process-pool work run in-process
  (pickle round-trip), plus `tests/` files themselves in coverage source; changed lines expanded to the smallest enclosing
  statement (header only for compound statements).
- *Impact:* line-level 2 misses → stmt-level 1 miss on sample; foundation of everything else.
- *Cost:* runtime 0.06 s to load a compact map (`covmap_compact.json.gz`, 350 KB gzip; 7 196 unique test-sets). Build: one
  coverage run (box: 73 s wall on 8 workers with xdist; HYPOTHESIS ≈2–4 min on the 4-core builder) — **commit the map** instead.
- *Risks:* order-dependent caches; setUpClass attribution; map/environment drift if built on a different Python. Build it with
  the exact lock file and python:3.13.
- *Implement:* Codex. *Validate:* `eval_baselines.py` stmt mode; recall vs full-suite probe on synthetic mutants.

### A2. Runtime probe of cheap candidates (exact failure measurement) — **VERIFIED exact on sample**
- *What:* fork from a warm parent (duckdb/pandas/numpy pre-imported, sqlglot not), install a meta-path finder that serves
  patched modules from memory and an `open()` redirect for patched data files, replace `ProcessPoolExecutor` with the synchronous
  pickle-round-trip executor, run candidates grouped by TestCase class with `unittest` (setUpClass once), per-class timeout
  via `SIGALRM`, report pass/fail over a pipe. Anything that crashes/times out ⇒ treated as failing (conservative).
- *Impact:* turns zero-fail commits with non-empty candidates into ∅ (sample: 2haz, 66ks, 2qn5, 5y72, fryt, gbav, 4d63, ktz3
  — ≈55 s of static cost removed) and shrinks live commits to exact failing sets. P1 reaches oracle simulated cost on the sample.
- *Cost:* box CPU ≈ 1.0–1.3 × recorded seconds + ~0.15 s per fork. Grouping by class cut CPU from 94 s → 25.6 s for the same probes.
- *Risks:* fidelity differences vs pytest (subtest semantics, `expectedFailure`, setUpModule, fixture-style pytest tests, order
  coupling); a probe-induced infinite loop (handled by timeout ⇒ fail); wall-time budget (must have a global budget guard).
- *Implement:* Claude owns the harness integration; Codex owns the probe policy. *Validate:* `eval_probe.py` must agree with labels
  for every probed test on all 30 sample commits; then synthetic mutants vs real pytest on a subset.

### A3. Import-time change analysis (names → readers → tests, narrowed by class and dialect)
- *What:* if a changed statement executes at import (module/class body, dict tables, `def` defaults/decorators), take the
  assigned names (plus string constants used as names), find runtime read sites (Attribute/Name/str-literal), follow import-time
  propagation (class-body tables built from that name) up to depth 3, map read lines to covering tests, then narrow: dialect files ⇒
  tests that instantiate that dialect or a subclass (runtime map ∪ coverage of its parser/generator/typing files); other class
  bodies ⇒ tests covering any line inside that class or its `<class>_sql` generator method or runtime references to the class.
  `def` signature changes ⇒ tests covering the function body.
- *Impact:* fixed 535z (WEEK_OFFSET), ktz3 (`TriggerProperties.arg_types`, 1493 → 3 candidates), and the two held-out misses.
- *Cost:* runtime index build ~1–2 s if done per run — **precompute the name index at build time** and ship it.
- *Risks:* dynamic `getattr`, metaclass magic (`_Dialect` builds tokenizer/parser tables at class creation), generic names
  (`arg_types`, `FUNCTIONS`) ⇒ big candidate sets (fine: the probe prunes them; cost is CPU).
- *Implement:* Codex. *Validate:* import-time mutant generator `mutants_importtime.py` + `recall_full.py`.

### A4. Expensive-test gating by "liveness" evidence
- *What:* never probe tests >2 s; include an expensive candidate iff at least one cheap candidate probe failed (change is live) or
  there is no cheap candidate covering the change. Optionally per-test thresholds from §1 (P(fail|live) ≫ break-even for all heavy tests measured).
- *Impact:* on synthetic data no case of "expensive fails, all cheap pass" at a 2 s threshold (0/29); saves 21–29 s on every dead
  change whose candidates include tpcds/max_nodes (sample 2haz, 66ks).
- *Risk:* the true conditional is unknown for unseen kinds; with 0/55 observed, a rough 95 % upper bound is ~5 % per such commit
  (rule of three), which is still below the tpcds break-even (7.2 %) but close to max_nodes' (9.6 %). Consider probing max_nodes'
  *cheap re-implementation* (A9) for parser/tokenizer core changes.

### A5. Cost-aware probing policy (sequential / EV-driven) — reduces CPU, the main remaining gap to the leaders
- *What:* stage 1 probes a cheap prefix (≤0.3 s) of the most likely killers; if live and expensive tests are being included anyway,
  remaining cheap candidates are added for free when they fit under the existing makespan; otherwise probe the rest. If stage 1 is all
  pass, probe the rest (needed to answer ∅). Skip probing when `CPU_est(P) > P(all pass) × cost(S)`.
- *Impact:* sample CPU 48.3 s → 16.3 s (box) but simulated 101.0 → 119.9 s: net ≈ −13 s on 30 commits. Priors need care (overfit risk).
- *Better version (HYPOTHESIS, to test):* a calibrated model P(test fails | features) using only generic features (stmt fan-in, file
  family, test family, is-import-time, mutation-free features like "line covered by this test's own module") trained on synthetic
  mutants — only scalars/coefficients shipped, never a patch→tests table.

### A6. Exact zero-test proofs (no CPU)
- *What:* (i) AST equality of old vs new module (handles comment/blank/formatting-only Python edits) with guards: the edit must not
  touch an encoding cookie on lines 1–2, must decode as UTF-8, and must not be in `tests/` source read textually; (ii) fixture lines
  starting with `--` (removed by `_filter_comments`) — only when the edit is entirely inside such lines; (iii) changes in statements
  with **no covering test and no import-time execution** (dead code) ⇒ ∅ (testmon principle: unexecuted code cannot influence a test);
  (iv) optional *trivial-compiler-equivalence*: compare compiled bytecode (`co_code`, consts, names) of changed functions.
- *Impact:* sample: 3 comment commits + comment parts of 3 multi commits handled at zero CPU.
- *Risk:* (iii) is unsafe for import-time code and for code reached only in subprocesses not captured — only apply when A1's capture is complete.

### A7. Makespan-optimal scheduling (exact for small sets)
- *What:* for each k=1..8, LPT then a local-improvement pass (swap/move) or exact DP when |S| small; choose min M+2k. Lower bound
  max(max_t, Σ/k). Almost always k=1 for small S (each extra worker costs 2 s; a second worker only helps if it cuts makespan >2 s).
- *Impact:* small but free; on the full suite LPT is already optimal (36.817 s at k=4 hits the max-test bound).

### A8. Function-level `__code__` hot-swap probes (CPU reduction, HYPOTHESIS)
- *What:* for in-function changes, pre-import sqlglot **and** the needed test modules in the warm parent; in the forked child,
  compile the patched module and assign `old_fn.__code__ = new_fn.__code__` for changed functions (identical free vars required;
  else fall back to fresh import). Removes ~0.1–0.15 s import per probe and lets the parent share loaded test modules.
- *Expected:* ~30 commits × ~0.15 s ≈ 4–5 s per batch saved; more for optimizer/executor test imports.

### A9. Sub-test-level probing for the heavy monolith tests (HYPOTHESIS, high upside)
- *What:* build-time coverage per *subtest/query* for `test_execute_tpcds` (99 queries), `test_execute_tpch`, `test_optimized_tpch`,
  optimizer `test_tpcds`/`test_tpch` and fixture monoliths (`check_file(..., only=title)` exists in tests/test_optimizer.py).
  At runtime, re-run only the queries/fixtures whose execution covers the changed statement; if they all pass, the test passes
  (the rest never execute the changed code). tpcds averages 21.454/99 ≈ 0.22 s per query.
- *Impact:* tpcds was included unnecessarily in ~30/51 live calibration cases (21.45 s each). If the final has a handful of such commits,
  this is worth tens of seconds. Needs care to replicate each test's assertion logic exactly.

### A10. Conservative fallbacks + global budget guard
- *What:* unknown file types (setup.py, pyproject, `tests/*.py` edits, new/deleted files, binary), parse failures, hunk mismatch,
  probe infra failure ⇒ widen in steps: (a) statement candidates without probe, (b) whole-file test set for `tests/` edits / all tests
  importing a changed helper, (c) full suite (36.817 s). Global wall-time guard: stop probing when elapsed > ~60 % of the limit
  (3 min practice / 4.5 min final) and answer remaining commits statically.
- *Impact:* bounds catastrophic outcomes; a rejected run (timeout) is worse than any miss.

(Also considered and ranked lower: pure static import graph — degenerates to the whole suite; ML predictive selection à la Facebook —
no historical data except synthetic, high overfit risk, allowed only as generic coefficients; LLM at runtime — no network, CPU-charged,
pointless.)

---

## 5. Missed-test risk management

**Failure sources we must cover (each VERIFIED to occur):** in-function code (coverage), subprocess work (syncpool), multi-line
statements (stmt expansion), import-time tables/constants (A3), dialect use without dialect-specific code (dialect map), `def` default
values (signature rule), fixture data (read map), shared class setup/cache (class expansion), serde/pickle paths (pickle round-trip in
both coverage and probe).

**Not yet covered — handle with fallbacks (HYPOTHESIS about final "unseen kinds"):**
- Edits to `tests/*.py` or `tests/helpers.py`: map by AST span of test methods (method edit ⇒ that test; class-level/helper/module-level
  edit ⇒ all tests in modules importing it; syntax error ⇒ whole file fails to import ⇒ all its tests).
- Syntax errors / import errors in sqlglot modules: "file cannot be imported" ⇒ **every test whose module imports it at collection**. Detect by
  compiling the patched module (cheap) and, for import-time changes, importing it in the probe child; on failure use the static import closure
  (`test_closure_static` / `_with_lazy`) to pick the test files.
- New files / deleted files / renamed functions / changed signatures used elsewhere ⇒ readers via name index.
- Performance regressions: `test_max_nodes` (28.8 s; checks node-count limits) and `test_generator_memory` (tracemalloc leak check across
  all dialect generators) fail on *behavioural* not functional changes; a change to caching/`__init__`/generator construction or the
  max-node counter should force-include them (rule keyed on what the code touches, e.g. tests covering the changed statement, not on the patch).
- Infinite loops ⇒ labelled as timeouts (HYPOTHESIS): probe timeout ⇒ include the test.
- Fixture comments: SQL comments are parsed and preserved by sqlglot ⇒ an added `/* */` or trailing `--` comment in a fixture SQL line
  changes outputs. Only whole lines beginning with `--` are dropped by `_filter_comments`.

**Decision policy:** default to inclusion whenever evidence is missing; never answer ∅ unless (AST-equal) or (every candidate probed and
passed and candidates are complete) or (provably dead code). Full suite only when the analysis itself fails.

---

## 6. Worker scheduling recommendations

- Cost = M + 2k; with typical selections of 1–40 small tests, **k=1 is optimal** unless a second worker cuts the makespan by >2 s.
- Use exact ms durations (`floor(seconds*1000+0.5)`), LPT per k ∈ [1, min(8,|S|)], then a 1-swap improvement; choose min.
- When an expensive test is included, the other workers have "free capacity" up to its duration: add cheap uncertain tests there instead
  of probing them (that is what P2's "include rest when marginal cost ≤ probe CPU" does).
- Full suite: k=4 (36.817 s) is optimal; never emit 8 workers for the full suite (44.8 s).

---

## 7. Handling unseen patches (generalisation)

- Everything is driven by maps of the **pinned** commit + generic analysis + runtime measurement; nothing keyed by patch content or edit kind.
- Validate on **synthetic mutants with different operators and file families** than the sample (our generators: comparison/boolean/
  constant/index/None/copy flags; import-time constants). Keep a held-out set that is never looked at while tuning.
- Prefer measured truth (probe) over heuristics for the precision step; prefer conservative inclusion for recall.
- Keep the shipped policy parameters few and physical (probe threshold B=2 s, CPU α, stage-1 size), not fitted to sample IDs.

---

## 8. Expected bottlenecks

1. **CPU of probes** — the gap to the leaders. Box: P1 48 s / P2 16 s per 30 commits; scorer CPU may be slower. Main sinks: dead changes
   in hub code with large candidate sets (4d63: 357 cands, 9 s), live changes probing many cheap tests (3utz: 10 s).
2. **Warm-up imports**: `import duckdb, pandas, numpy` ≈ 1.1 s CPU (measured with threads); set OPENBLAS/OMP threads to 1.
3. **Wall time**: 3 min / 4.5 min with 2 cores; P1 wall ≈ 49 s for 30 commits on the box.
4. **Read-only FS**: precompile `.pyc` at build (`compileall`), `PYTHONDONTWRITEBYTECODE=1`; patched modules compiled in memory.
5. **Repo size**: line map JSON 14 MB raw ⇒ use compact/gzip (350 KB).
6. **Fidelity**: unittest-in-process vs labels-by-pytest; process-pool semantics; class-level caches.

---

## 9. Proposed experiments (in priority order)

| # | Experiment | Metric | Status |
|---|---|---|---|
| E1 | Fresh held-out synthetic mutants (import-time + in-function), full-suite probe truth vs static candidates | commit-level miss rate | **done** — see table below |
| E2 | `make sample` with a Docker image built from the prototype (exact runtime env, nobody, read-only, 2 CPUs) | simulated, misses, container CPU (`docker stats`/cgroup cpu.stat) | todo (Claude) |
| E3 | P1 vs P2 vs P2+`__code__` swap CPU on sample + 100 synthetic mutants | Σ(simulated+CPU) | todo (Codex) |
| E4 | Probe fidelity vs real pytest on 50 synthetic mutants (run real `pytest` on candidates in patched copy) | disagreement count (must be 0) | todo |
| E5 | Heavy-test sub-query probing for tpcds/tpch (A9) | unnecessary heavy inclusions avoided, CPU | todo |
| E6 | Syntax-error / import-error / tests-file / new-file patches | misses (must be 0) | todo |
| E7 | Wall-time stress: 90 commits (final×1.5 safety) under `--cpus 2` | wall < 4.5 min | todo |

**E1 result (fresh held-out set `mutants_full2`, ground truth = all tests except the 3 heaviest):** see appended section at the end.

---

## 10. Claude & Codex implementation plans (non-overlapping)

Repository layout proposal (new team repo; do **not** touch existing repos without Prajay's go-ahead):
```
Dockerfile                  (Claude)
selector/main.py            (Claude)  entry point, I/O, budget guard, fallbacks, output validation
selector/patchparse.py      (Claude)  unified-diff parser + in-memory apply (+ fuzzy relocate)
selector/schedule.py        (Codex)   cost/scheduler (A7)
selector/candidates.py      (Codex)   stmt mapping, import-time analysis, fixtures, tests/ edits (A1,A3,A6)
selector/probe.py           (Codex)   probe runner core (A2) — Claude integrates/guards it
selector/policy.py          (Codex)   probe/include decisions (A4,A5)
maps/covmap.json.gz, maps/names.json.gz, maps/dialects.json, maps/reads.json, maps/tests_meta.json  (Codex, generated)
tools/build_maps/*.py       (Codex)   coverage + read + dialect collection (from artifacts/scripts)
tests_local/                (Claude)  sample scorer harness, synthetic mutant harness, format checks
```
Branches: `claude/integration` (Dockerfile, main.py, patchparse.py, tests_local/), `codex/algorithms` (everything under maps/, tools/,
candidates.py, policy.py, probe.py, schedule.py). Merge to `main` only by Prajay's decision. Interfaces are frozen below so the branches never edit the same file.

**Interfaces / data formats**
- `patchparse.parse(text) -> list[FilePatch]`, `FilePatch{old_path,new_path,status:'M'|'A'|'D'|'R',hunks:[Hunk{old_start,old_len,new_start,new_len,lines:[(tag,text)]}]}`;
  `patchparse.apply(base_text, FilePatch) -> str` (raise `PatchMismatch` on context mismatch).
- `candidates.analyze(patch: list[FilePatch], base_root: str, maps) -> Analysis{cands: set[int], must: set[int], zero_proof: bool, fallback: None|'file'|'full', notes: list}`
- `probe.run(base_root, files: dict[path,str], test_ids: list[str], timeout_s, cpu_budget_s) -> dict[test_id, 'pass'|'fail'|'timeout'|'error']`
- `policy.decide(analysis, tests_meta, probe_fn, budget) -> set[int]`
- `schedule.best(selected: set[int], durations_ms: dict[int,int]) -> list[list[int]]` (empty list for ∅)
- Maps: `covmap.json.gz = {"sets":[[test numbers]...], "files": {path: {line: set_index}}}`;
  `names.json.gz = {name: [[file, line, runtime(0/1), [assigned names if import-time]]]}`;
  `dialects.json = {"sqlglot.dialects.<stem>.<Class>": [test numbers]}`; `reads.json = {relpath: [test numbers]}`;
  `tests_meta.json = {number: {id, file, class, seconds, probe_cpu_est}}`. All maps keyed by **test id**, translated to numbers at runtime
  from `/data/tests.json` (numbers could differ in the hidden set; ids are stable — HYPOTHESIS, so map by id to be safe).

### CLAUDE — Lead Integration Engineer brief
Scope: architecture glue, main execution, reliability, Docker, submission readiness. Branch `claude/integration`.
1. **Dockerfile**: `FROM python:3.13-slim@sha256:e48f59…` (kit digest — it decides which tests run); `pip install -r requirements.lock`
   (needed for probing: duckdb, pandas, numpy, pytest…); clone sqlglot at eb11aec (git clone + checkout, or GitHub archive of the commit);
   `python -m compileall -q sqlglot tests`; copy `selector/` + `maps/`; `chmod -R a+rX`; `ENV PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 SKIP_INTEGRATION=0`;
   `CMD ["python","-S"?, "/app/selector/main.py"]` (keep site; just minimal imports in parent until warm-up).
2. **main.py**: read tests.json (map ids→numbers; ignore `skipped`), list patches sorted, for each: parse → analyze → decide → schedule → print one
   JSON line immediately (flush) — **all logs to stderr**; wrap each commit in try/except ⇒ on any exception emit full-suite (k=4) for that commit;
   ensure every commit printed exactly once; validate numbers/duplicates/≤8 workers before printing.
3. **Budget guard**: wall clock limit param (default 150 s for ≤45 commits, scale 4.5 min for more: `limit = 3.0 min if n<=45 else 4.5 min`, use 60 %); when
   exceeded, policy gets `budget=0` ⇒ static candidates only. Also guard total CPU via `resource.getrusage`.
4. **patchparse.py**: robust parser (no-newline markers, `/dev/null`, renames, binary, CRLF), in-memory apply, relocation if hunk offsets drift
   (match context uniquely) else `PatchMismatch` ⇒ fallback.
5. **Local harness** (`tests_local/`): `make sample` equivalent with docker flags from the kit Makefile; per-commit diff vs answers; CPU measurement
   via `docker run` + `/sys/fs/cgroup/cpu.stat` or `/usr/bin/time` inside; synthetic-mutant scorer (uses Codex's generators).
6. **Acceptance**: `make check` format OK; `make sample` 0 misses; container CPU on sample ≤ 20 s (target ≤ 10 s); wall ≤ 60 s; a 90-commit synthetic batch
   finishes < 150 s wall with `--cpus 2`; rejects nothing on malformed patches (fuzz 200 random diffs ⇒ always valid JSON lines).
7. **Submission readiness**: public repo, Dockerfile at root, maps committed (<20 MB), build from scratch < 3 min, no network at runtime, no
   downloads except PyPI/sqlglot pinned. Prajay submits (we never submit).

### CODEX — Advanced Algorithm Engineer brief
Scope: coverage collection, dependency propagation, AST patch analysis, risk model, scheduling. Branch `codex/algorithms`.
1. **Map builders** (`tools/build_maps/`, start from `artifacts/scripts/`): coverage with `--cov-context=test`, `-p syncpool` (in-process pools with
   pickle round-trip), include `tests/` in `source`, attribute setup-phase contexts to all tests of the class; fixture/data reads via audit hook;
   dialect instantiation map; name index (`importtime.Index`) precomputed; per-test in-process probe CPU estimates (run each class once in-process,
   time it). Output formats as above. Acceptance: maps reproduce `eval_baselines.py stmt+import` = 0 misses on sample and 0/48 on `mutants_full`.
2. **candidates.py**: stmt expansion; import-time analysis (names, string constants, propagation depth 3, class filter incl. methods + `<cls>_sql`,
   dialect filter = runtime map ∪ coverage of parsers/generators/typing/tokenizers for the dialect and its subclasses); `def` header changes ⇒ body
   callers; AST-equal ⇒ zero proof (with cookie/utf-8 guards); fixtures via read map (+ `--`-line rule); `tests/` edits by method spans; syntax-error ⇒
   import-failure set from the static import closure; unknown files ⇒ fallback flag.
3. **policy.py**: P1 baseline (probe all ≤B=2 s, include >B iff live) then P2 (cost-aware) — must keep 0 misses on all local sets; tune only B, α, stage-1
   size on synthetic train set; report on held-out set.
4. **probe.py**: from `artifacts/scripts/probe.py` (class-grouped suites, SIGALRM timeouts, crash ⇒ fail); add `__code__` hot-swap fast path (A8) behind a flag;
   add sub-test probing for tpcds/tpch/optimizer monoliths (A9) as a stretch.
5. **schedule.py**: exact-ms LPT for k=1..8 + improvement pass; unit tests incl. full suite ⇒ 36.817 s at k=4.
6. **Acceptance / perf targets**: sample simulated ≤ 100 s with 0 misses; probe CPU ≤ 15 s on sample (box), stretch ≤ 8 s; held-out synthetic set 0 misses;
   per-commit analysis (without probe) ≤ 30 ms; map load ≤ 0.1 s.

---

## Prioritised first five changes

1. **Ship the static hybrid candidate generator with committed maps** (stmt coverage with in-process pools, import-time name analysis with
   class + runtime-dialect filters, signature rule, fixture read map, AST-equal ⇒ ∅) and a full-suite fallback per commit. Alone: 174.8 s / 0 misses on sample.
2. **Add the runtime probe (P1) for candidates ≤2 s, class-grouped, with timeouts and crash ⇒ fail; include >2 s candidates only when a probe failed.**
   Sample: 101.0 s simulated + ~48 s box CPU, 0 misses; exact agreement with labels.
3. **Docker + budget guard + robust patch parser + `make sample` CI** (Claude) — get a valid, safe submission on the board early (tie rule rewards early).
4. **Cut probe CPU** (P2 cost-aware policy, `__code__` hot-swap, per-test in-process CPU estimates, include-cheap-under-heavy-makespan) — target ≤10 s CPU on sample.
5. **Close unseen-kind gaps before the deadline**: `tests/*.py` edits, syntax/import errors, new/deleted files, perf-test triggers (max_nodes, generator_memory),
   then re-run a fresh held-out synthetic set; only then consider A9 (sub-test probing of heavy tests).

---

## Sources
- Track page (rules, scoring, limits, board): https://www.joinbuilders.org/buildathon/tracks/blast-radius
- Kit: `kit/SCORING.md`, `kit/README.md`, `kit/score.py`, `kit/Makefile`, `kit/Dockerfile`, `kit/selector.py`, `kit/requirements.lock`, `kit/sample/*`
  (downloaded from https://www.joinbuilders.org/buildathon/tracks/blast-radius/files/blast-radius-sample.tar.gz and `/files/SCORING.md`; identical SCORING.md).
- sqlglot @ eb11aec: https://github.com/tobymao/sqlglot
- Ekstazi (dynamic file-dependency RTS): Gligoric, Eloussi, Marinov, ISSTA 2015, https://dl.acm.org/doi/10.1145/2771783.2771784
- STARTS (static class-level RTS): Legunsen, Shi, Marinov, ASE 2017 tool, https://www.cs.cornell.edu/~legunsen/pubs/LegunsenETAL17STARTS.pdf
- pytest-testmon (coverage-context, block checksums; "unexecuted code can't affect a test"): https://www.testmon.org/blog/determining-affected-tests/
- Facebook Predictive Test Selection (ML; >95 % test recall, >99.9 % change recall, ~1/3 tests): https://arxiv.org/abs/1810.05286
- Google TAP (transitive-closure RTS; failing tests are "closer" to changed code): Memon et al., ICSE-SEIP 2017, https://research.google/pubs/taming-google-scale-continuous-testing/
- Scheduling: Graham's LPT bound (4/3 − 1/(3m)) — classical result (Graham 1969).
- Mutation-testing concepts used for local validation (equivalent mutants, weak mutation, trivial compiler equivalence) — standard literature (Howden 1982; Papadakis et al. ICSE 2015).
- Public Blast Radius team READMEs skimmed for **ideas only** (no code read or copied): jvenkumahanthi-coder/blast-radius, Rizwanzaman70/Bizz01-BlastRadius,
  shobhit1kapoor/blast-radius-impact-selector, StrikerX99/undertakers-blast-radius-v1. Common ideas there: per-test coverage incl. process-pool work, AST
  comment detection, import-time name analysis, fixture read maps, runtime probing of small groups (undertakers). One README self-reports 141.6 s
  simulated on the 30 sample commits — our oracle there is 94.35 s, so that number is consistent with non-trivial waste; another adds handlers for specific
  constants (WEEK_OFFSET / NUMERIC_ESCAPES) — we consider that overfitting and a rule risk.

---

## Appendix — E1 result (fresh held-out set `mutants_full2`)
56 fresh synthetic mutants (made after the two generic fixes, never used for tuning). Ground truth = probe of the whole suite except the 3 heaviest tests (`artifacts/recall_full2.jsonl`).
- 45/56 broke at least one test; 11/56 broke nothing.
- **Static hybrid commit-level misses: 0/56.** VERIFIED from the jsonl.
- Caveat: the 3 heaviest tests were excluded from ground truth, and synthetic mutants are not the same as the real final's "new kinds of change"; E6 (syntax/import errors, tests-file edits, new files) is still untested.
