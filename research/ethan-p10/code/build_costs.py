"""BUILD TIME (free): measure the real CPU each test costs inside a warm forked probe child at the pinned commit
(fork + unittest run, class state from preset_cache). Usage: python build_costs.py <sqlglot> <tests.json> <preset.pkl> <out.json> [workers]"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
src, tj, pc, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3]), os.path.abspath(sys.argv[4])
W = int(sys.argv[5]) if len(sys.argv) > 5 else 4
tests = json.load(open(tj))['tests']
import fastprobe as fp
SKIP = set()   # measure every test (heavy ones too)
mods = sorted({t['file'][:-3].replace('/', '.') for t in tests})
fp.warm(src, mods)
fp.load_presets(pc, sorted({(t['file'][:-3].replace('/', '.'), t['id'].split('::')[1]) for t in tests}))
plan = ('hot', [], set())
todo = [t for t in tests if t['number'] not in SKIP]; res = {t['number']: t['seconds'] for t in tests if t['number'] in SKIP}
running = {}
def reap():
    pid, st, ru = os.wait4(-1, 0)
    n = running.pop(pid); res[n] = round(ru.ru_utime + ru.ru_stime, 4)
for t in todo:
    while len(running) >= W: reap()
    r, w = os.pipe(); pid = os.fork()
    if pid == 0:
        os.close(r); dn = os.open(os.devnull, os.O_WRONLY); os.dup2(dn, 1); os.dup2(dn, 2)
        try: fp.child({}, [t['id']], plan, 60.0, w, False)
        finally: os._exit(3)
    os.close(w); os.close(r); running[pid] = t['number']
while running: reap()
json.dump({str(k): v for k, v in sorted(res.items())}, open(out, 'w'))
print('done', len(res))
