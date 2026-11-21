"""Static candidate recall on sample + held-out mutants. Usage: BR_DEFUSE=0|1 python static_recall.py <jsonl:patchdir> ..."""
import sys, os, json
pairs = sys.argv[1:]
sys.argv = [sys.argv[0], '../kit', '../sqlglot', '../artifacts']
os.environ.setdefault('BR_ARTIFACTS', '/workspace/blast/artifacts')
import eval_baselines as eb
from pathlib import Path
T = {t['number']: t for t in eb.tests}
sec = lambda s: sum(T[n]['seconds'] for n in s)
# sample
tot = 0; miss = 0; csec = 0
for cid in sorted(eb.ans):
    cand, _ = eb.select((eb.kit/f'sample/commits/{cid}.patch').read_text(), 'stmt+import')
    m = not set(eb.ans[cid]['failing']) <= cand; miss += m; csec += sec(cand)
    tot += eb.schedule(cand)[0]/1000 + 300*m
print(f'sample: static_sim={tot:.3f} misses={miss} cand_sec={csec:.1f}')
for pr in pairs:
    jl, d = pr.split(':'); rows = [json.loads(l) for l in open(jl)]
    miss = []; csec = 0; ncand = 0
    for r in rows:
        cand, _ = eb.select((Path(d)/r['mutant']).read_text(), 'stmt+import')
        lost = set(r['fail']) - cand
        if lost: miss.append((r['mutant'], sorted(lost)[:5]))
        csec += sec(cand); ncand += len(cand)
    print(f'{jl}: n={len(rows)} broke={sum(bool(r["fail"]) for r in rows)} commit_misses={len(miss)} cand_sec={csec:.1f} ncand={ncand} {miss[:6]}')
