import sys,json,os
sys.argv=['x','kit','sqlglot','artifacts']; sys.path.insert(0,'opt2'); os.environ['BR_IDXCACHE']='opt2/idx_cache.pkl'
import eval_baselines as eb
from pathlib import Path
sets=[('kit/sample/commits','kit/sample/answers.json'),('mutants_full','opt2/ans_full.json'),('mutants_full2','opt2/ans_full2.json'),('opt2/mutants_full3','opt2/ans_full3.json')]
extra=os.environ.get('EXTRA')
if extra: sets+= [tuple(x.split(':')) for x in extra.split(',')]
for d,a in sets:
    ans=json.load(open(a))['commits']; miss=[]; tot=0
    for pf in sorted(Path(d).glob('*.patch')):
        if pf.stem not in ans: continue
        c,notes=eb.select(pf.read_text(),'stmt+import'); t=set(ans[pf.stem]['failing'])
        if pf.stem=='4d63': print('4d63 cand',len(c))
        tot+=len(c)
        if not t<=c: miss.append(pf.stem)
    print(d,'static misses',miss,'total cand',tot)
