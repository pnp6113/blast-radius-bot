"""Recompute static-candidate recall (current code) against full-suite probe truth in recall_full.jsonl."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = [sys.argv[0], 'kit', 'sqlglot', 'artifacts']
import eval_baselines as eb
from pathlib import Path
rows = [json.loads(l) for l in open(os.environ.get('RECALL','artifacts/recall_full.jsonl'))]
kinds = {'i': 'import-time const', 'm': 'in-function'}
agg = {}
for r in rows:
    patch = Path(os.environ.get('MDIR','mutants_full'), r['mutant']).read_text()
    cand, notes = eb.select(patch, 'stmt+import')
    fail = set(r['fail']); missed = sorted(fail - cand)
    k = kinds[r['mutant'][0]]
    a = agg.setdefault(k, dict(n=0, killed=0, commit_miss=0, tests_fail=0, tests_missed=0, cand_sizes=[]))
    a['n'] += 1; a['killed'] += bool(fail); a['commit_miss'] += bool(missed); a['tests_fail'] += len(fail); a['tests_missed'] += len(missed); a['cand_sizes'].append(len(cand))
    if missed: print('MISS', r['mutant'], r['file'], 'fail', sorted(fail)[:6], 'missed', missed[:6], str(notes)[:200])
for k, a in agg.items():
    cs = sorted(a.pop('cand_sizes')); a['median_cand'] = cs[len(cs)//2]; print(k, a)
