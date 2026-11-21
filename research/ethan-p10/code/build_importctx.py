"""Build-time map (pinned commit only): lines executed OUTSIDE any test context (import / collection), from the
per-test-context coverage DB. Usage: python build_importctx.py <.coverage> <sqlglot_dir> <out.json>"""
import sys, json, coverage, os
d = coverage.CoverageData(sys.argv[1]); d.read(); src = os.path.abspath(sys.argv[2])
d.set_query_contexts([''])
out = {}
for f in d.measured_files():
    rel = os.path.relpath(f, src) if os.path.isabs(f) else f
    if rel.startswith('sqlglot/'): out[rel] = sorted(d.lines(f) or [])
json.dump(out, open(sys.argv[3], 'w'))
print(len(out), sum(map(len, out.values())))
