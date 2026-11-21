"""Local validation: for synthetic mutants, probe ALL stmt-level candidate tests (incl. expensive) to get ground
truth under our probe, then measure how often an expensive test fails while every cheap candidate passes.
Usage: python calibrate.py <kit> <sqlglot> <artifacts> <mutant_dir> <out_jsonl> <workers>"""
import sys, os, json, time, resource, multiprocessing as mp
sys.path.insert(0, os.path.dirname(__file__))
from pathlib import Path
kit, src, art, mdir, outp, W = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]), sys.argv[5], int(sys.argv[6])
def work(pf):
    import eval_baselines as eb, probe
    tests = {t['number']: t for t in eb.tests}
    patch = pf.read_text(); cand, _ = eb.select(patch, 'stmt')
    files = {f['new']: eb.apply((Path(src)/f['new']).read_text(), f) for f in eb.parse_patch(patch)}
    c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    res = probe.probe(src, files, [tests[n]['id'] for n in cand], per_test_timeout=120) if cand else {}
    c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    fails = sorted(n for n in cand if res.get(tests[n]['id']) != 'pass')
    return {'mutant': pf.name, 'file': eb.parse_patch(patch)[0]['new'], 'cand': sorted(cand), 'fail': fails,
            'cpu': (c1.ru_utime+c1.ru_stime)-(c0.ru_utime+c0.ru_stime)}
if __name__ == '__main__':
    sys.argv = [sys.argv[0], kit, src, art]  # eval_baselines reads argv on import
    import eval_baselines, probe; probe.warm()
    pfs = sorted(mdir.glob('*.patch'))
    with mp.get_context('fork').Pool(W) as pool, open(outp, 'w') as fo:
        for r in pool.imap_unordered(work, pfs):
            fo.write(json.dumps(r) + '\n'); fo.flush()
