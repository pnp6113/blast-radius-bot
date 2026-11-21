"""Convert coverage.py context DB into line->tests map. Output: {file: {line: [test numbers]}}, plus per-test line counts."""
import sqlite3, json, sys, collections
db, tests_json, out = sys.argv[1:4]
tests = json.load(open(tests_json))['tests']
num = {t['id']: t['number'] for t in tests}
con = sqlite3.connect(db)
ctx = dict(con.execute('select id, context from context'))
files = dict(con.execute('select id, path from file'))
ctxnum = {}
unmapped = collections.Counter()
for cid, c in ctx.items():
    base = c.rsplit('|', 1)[0] if '|' in c else c
    phase = c.rsplit('|', 1)[1] if '|' in c else ''
    if base in num: ctxnum[cid] = num[base]
    else: unmapped[c[:60]] += 1
print('contexts', len(ctx), 'mapped', len(ctxnum), 'unmapped sample', unmapped.most_common(5))
from coverage.numbits import numbits_to_nums
m = collections.defaultdict(lambda: collections.defaultdict(set))
nocontext = collections.defaultdict(set)
for fid, cid, nb in con.execute('select file_id, context_id, numbits from line_bits'):
    path = files[fid]
    lines = numbits_to_nums(nb)
    if cid in ctxnum:
        for l in lines: m[path][l].add(ctxnum[cid])
    else:
        nocontext[path].update(lines)
res = {f: {str(l): sorted(s) for l, s in d.items()} for f, d in m.items()}
json.dump({'line_tests': res, 'lines_without_test_context': {f: sorted(s) for f, s in nocontext.items()}}, open(out, 'w'))
print('files', len(res), 'covered lines', sum(len(d) for d in res.values()))
