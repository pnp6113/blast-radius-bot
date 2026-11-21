import sys,json,os
sys.argv=['x','kit','sqlglot','artifacts']; sys.path.insert(0,'opt2'); os.environ['BR_IDXCACHE']='opt2/idx_cache.pkl'
import eval_baselines as eb
from pathlib import Path
for d,a in [('kit/sample/commits','kit/sample/answers.json'),('mutants_full','opt2/ans_full.json'),('mutants_full2','opt2/ans_full2.json'),('opt2/mutants_full3','opt2/ans_full3.json')]+[(x,y) for x,y in [a.split(':') for a in sys.argv_extra]] if False else [('kit/sample/commits','kit/sample/answers.json'),('mutants_full','opt2/ans_full.json'),('mutants_full2','opt2/ans_full2.json'),('opt2/mutants_full3','opt2/ans_full3.json')]:
    ans=json.load(open(a))['commits']; n=0; miss=0
    for pf in sorted(Path(d).glob('*.patch')):
        if pf.stem not in ans: continue
        c,notes=eb.select(pf.read_text(),'stmt+import')
        if any(isinstance(x,tuple) and x[0]=='lambda-body' for x in notes):
            n+=1; t=set(ans[pf.stem]['failing']); ok=t<=c
            miss+= not ok; print(d,pf.stem,len(c),len(t),'OK' if ok else 'STATIC-MISS', [x for x in notes if isinstance(x,tuple)][:2])
    print(d,'lambda-path commits',n,'static misses',miss)
