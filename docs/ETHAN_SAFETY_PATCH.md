# Combined safety patch for feat/cursor-speed (local only, nothing pushed)

Main deliverable: combined-on-1a4941a.diff  (apply: `git checkout 1a4941a && git apply combined-on-1a4941a.diff`)
Full patched tree: /workspace/brb-combined

Contents (all in blast/select_p10.py unless noted):
1. Fallbacks to every test: post-patch compile() failure in sqlglot/*.py, A/D/R/binary/missing-file/apply errors,
   tests/*.py or conftest edits; per-commit try/except keeps one answer line per commit.
2. Narrow all-pass rule (all three all-pass branches incl. 1a4941a's one-fork path): keep an unprobed slow test only if
   (i) its source contains a string literal/identifier that the diff changed, or
   (ii) no probed cheap test covered a changed line -> keep slow tests that cover the changed lines.
3. Package-walk edge: tests that pkgutil-walk a sqlglot package (test_no_redundant_expression_metadata) are scheduled
   directly for edits in that package (+0.022 s sim on sample; BLAST_P10_PKGWALK=0 disables -> 94.351).
4. Dialect-witness rule (2f1a860) restricted to edits inside a `class Tokenizer` body and widened to subclass dialects.
5. Import probe no longer skipped for constants-only edits (bool flip in expressions arg_types failed 1115 tests at import).
6. tests/test_ethan_safety.py, tests/test_allpass_narrow.py. P2 blanket keep from feat/ethan-safety-da5b NOT included.
Older: combined-on-2f1a860.diff, combined-on-49af839.diff (superseded), safety_vs_9479d30.diff (= the da5b branch diff).
