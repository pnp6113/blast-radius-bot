"""Static hybrid selector only (no probe), whole process: prints JSONL like a submission + sim to stderr."""
import sys, os, json
sys.argv = sys.argv[:4]
import eval_baselines as eb
tot = 0; miss = 0
for cid in sorted(eb.ans):
    cand, _ = eb.select((eb.kit / f'sample/commits/{cid}.patch').read_text(), 'stmt+import')
    c, w = eb.schedule(cand); print(json.dumps({'commit': cid, 'workers': w}))
    m = not set(eb.ans[cid]['failing']) <= cand; tot += c / 1000 + 300 * m; miss += m
print(f'static SIMULATED={tot:.3f} misses={miss}', file=sys.stderr)
