"""Ground truth for the 3 heavy tests on held-out mutants where they are static candidates and the rest of the suite passes."""
import sys, os, json, multiprocessing as mp
sys.argv = [sys.argv[0], '../kit', '../sqlglot', '../artifacts']
os.environ.setdefault('BR_ARTIFACTS', '/workspace/blast/artifacts')
import eval_baselines as eb, probe
from pathlib import Path
T = {t['number']: t for t in eb.tests}; HEAVY = {1490, 1106, 1224}
src = str(Path('../sqlglot').resolve())
def work(job):
    pf, big = job; hv = [n for n in big if n in HEAVY]
    if not hv: return (pf, [], [])
    patch = Path(pf).read_text()
    files = {f['new']: eb.apply((Path(src)/f['new']).read_text(), f) for f in eb.parse_patch(patch)}
    r = probe.probe(src, files, [T[n]['id'] for n in hv], per_test_timeout=120)
    return (pf, hv, [n for n in hv if r.get(T[n]['id']) != 'pass'])
if __name__ == '__main__':
    probe.warm()
    jobs = json.load(open('heavy_jobs.json'))
    with mp.get_context('fork').Pool(4) as p:
        for res in p.imap_unordered(work, jobs): print(json.dumps(res), flush=True)
