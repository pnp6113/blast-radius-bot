import sys, os, json
pairs = sys.argv[1:]
sys.argv = [sys.argv[0], '../kit', '../sqlglot', '../artifacts']
import eval_baselines as eb
from pathlib import Path
for pr in pairs:
    jl, d, m = pr.split(':')
    r = [json.loads(l) for l in open(jl) if m in l][0]
    cand, notes = eb.select((Path(d)/r['mutant']).read_text(), 'stmt+import')
    print(m, 'lost', sorted(set(r['fail'])-cand)[:5], len(cand), notes)
