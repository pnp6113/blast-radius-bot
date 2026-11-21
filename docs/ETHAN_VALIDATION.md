# Validation of Ethan's static hybrid and P2

Branch `feat/cursor-ethan-validation`, based on `integrate/ethan-hybrid` (`0e11d86`). That branch contains `blast/patchparse.py` only. The selector under test is the research tree `scripts/eval_baselines.py` mode `stmt+import`, plus the P2 decision in `scripts/eval_policy2.py`. Neither was modified.

`main` and PR #1 are untouched. This is not a submission.

Reproduce:

```text
python tools/validate_ethan.py
python -m unittest discover -s tests -p test_ethan_validation.py -v
```

`BLAST_ETHAN_ROOT` defaults to `D:\Hackathon\ethan-blast\blast-radius-research`. `BLAST_SQLGLOT` defaults to this repo's pinned checkout. The machine-readable report is `experiments/ethan/validation.json`.

The lower bound is generic. A syntax error selects every catalog test whose module-level imports reach the broken file, plus tests that walk `sqlglot.typing` with `import_module` on an f-string, plus tests that load `sqlglot.dialects.<Class>` by attribute or by an import inside the test. Test numbers are looked up after the rule matches.

## What already holds

- Changing the max-nodes error text in `sqlglot/parser.py` keeps the test whose source contains `Maximum number of AST nodes`. Static selection is 1489 tests, 0 misses of that rule. The selection is far wider than the one assertion.
- An edit to `tests/test_parser.py` falls back to all 1620 tests.
- An unseen fixture edit, `tests/fixtures/identity.sql`, follows the file-read map (2 tests) and matches it.
- `blast/patchparse.py` classifies a new file as status `A` and a deletion as status `D`. The selector does not call it.

## Misses

| Patch Ethan has not seen | Selected | Lower bound missed | Priority tests missed |
| --- | ---: | ---: | --- |
| Syntax error inside `sqlglot/typing/snowflake.py` | 2 | 1020 | `test_no_redundant_expression_metadata` |
| Syntax error inside `sqlglot/dialects/snowflake.py` | 227 | 834 | `test_dialect_attribute_access_is_cached` |
| Syntax error on line 1 of `sqlglot/dialects/bigquery.py` | 0 | 1031 | every test that imports that module at collection |
| Syntax error on the nested `import sqlglot.dialects.tsql` line in `sqlglot/generator.py` | 2 | 1615 | `test_generator_memory`, `test_max_nodes` |
| Syntax error on the nested Athena/Presto imports in `pushdown_predicates.py` | 94 | 1523 | `test_generator_memory`, `test_max_nodes` |
| Delete `sqlglot/dialects/clickhouse.py` | 0 | 42 | the ClickHouse test module |
| Add `sqlglot/dialects/adversarial_unseen.py` | raises `FileNotFoundError` | the run dies | no JSON line is produced |

A real import, not the probe, confirms the Snowflake case. Overlaying `def !!!` at the top of `sqlglot/dialects/snowflake.py` and importing `tests.dialects.test_dialect` raises `SyntaxError: invalid syntax`. That module imports Snowflake at import time, and other dialect test modules import `tests.dialects.test_dialect`. Collection fails for those tests. The selector still returns a partial line-coverage set (227) and continues. One missed failing test is a 300 second penalty. A traceback instead of a JSON line rejects the run.

`test_dialect_attribute_access_is_cached` is not a static importer of `snowflake.py`. It loads `sqlglot.dialects.Snowflake` through `__getattr__`. It is outside the 227.

The nested TSQL import and the nested Athena/Presto imports are inside functions. A syntax error still fails `compile()` of the whole module, so every test that imports `generator.py` or `pushdown_predicates.py` fails. Line coverage of those lines returns 2 and 94 tests.

## P2 drops the test that actually fails

Static selection of the max-nodes message change includes `tests/test_parser.py::TestParser::test_max_nodes`. P2 never probes a test slower than 2 seconds. When every cheaper probe returns pass, `eval_policy2.py` takes the branch `sel = set(); how = 'all pass'` and drops the slow tests. Replaying that branch on this candidate set keeps **0** tests and drops `test_max_nodes`.

That test is the one whose source contains the sentence that was edited. Cheap tests do not assert it, so they pass, and the only failure is the one P2 discards. The same hole applies to `test_generator_memory` (6.686 s): it is past the 2 second probe cutoff, and it is absent from the line-coverage set of the nested TSQL import.

P2 was not executed in-process here. `probe.py` uses `os.fork` and `signal.SIGALRM`, which this Windows host does not provide. The drop is the policy code with every probe forced to pass. Simulated seconds would look better. The miss costs 300 seconds.

## Generic fixes

1. If `compile()` fails on a patched `.py` file, or the file is deleted, select the import lower bound above. Do not consult line coverage for a module that cannot be imported. Until that closure is reviewed, fall back to every test. A partial coverage set is the miss.
2. Catch `FileNotFoundError`, `PatchError`, and `PatchMismatch` per file and fall back to every test for that commit. Wire `blast.patchparse` in front of `select`. Status `A`, `D`, `R`, and binary diffs take the fallback. A new file must not read a path that does not exist.
3. Change the P2 `all pass` branch so a static candidate slower than the probe cutoff stays selected. Probes that were not run are not evidence the test passes. `test_max_nodes` and `test_generator_memory` are the two that move the score, and both sit above 2 seconds.
4. Keep the typing-package walk and the lazy `sqlglot.dialects.<Class>` load as explicit edges, including for a one-line edit that coverage never saw. The Snowflake attribute test and `test_no_redundant_expression_metadata` are the examples.
5. Leave comment-only Python diffs alone only when both versions parse and their ASTs match. A fixture comment is not that case. The identity-fixture edit correctly stayed on the read map.

Ship the static message-change behavior only with fix 3 if P2 is on. Without fix 1 and fix 2, an unseen syntax error or a new file is a missed failure or a crashed run.
