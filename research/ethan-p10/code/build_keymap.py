"""BUILD TIME (free): key-level read map for class-attribute dict literals at the pinned commit.
Usage: python build_keymap.py <sqlglot> <tests.json> <out.pkl> [workers] [hashseed-tag]
Each test runs alone in a fork of a parent that imported sqlglot + all test modules with keytrack installed.
Output pickle: {'dicts': {name: keyreprs}, 'import': set((name,key,kind)), 'tests': {num: frozenset((name,key,kind))},
 'unknown': set(test nums whose tracked run did not pass / crashed / timed out), 'keyexpr': {(name, unparse(keyexpr)): keyrepr}}"""
import sys, os, json, pickle, signal, unittest, importlib, time, ast
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keytrack as K
K.install()
src, tj, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3])
W = int(sys.argv[4]) if len(sys.argv) > 4 else 4
tests = json.load(open(tj))['tests']
import fastprobe as fp
mods = sorted({t['file'][:-3].replace('/', '.') for t in tests})
fp.warm(src, mods)
from pathlib import Path as _P
for p in sorted(_P(src, 'sqlglot').rglob('*.py')):
    if p.name == '__main__.py': continue
    m = str(p.relative_to(src))[:-3].replace('/', '.').removesuffix('.__init__')
    try: importlib.import_module(m)
    except BaseException: pass
IMPORT = set(K.READS)
# key expression -> keyrepr, by evaluating every key expr of every class-level dict literal in its module globals
KEYEXPR = {}
def scan(modname, path):
    try: tree = ast.parse(open(path).read())
    except Exception: return
    g = sys.modules[modname].__dict__
    def walk(body, qual):
        for st in body:
            if isinstance(st, ast.ClassDef): walk(st.body, qual + [st.name])
            elif qual and isinstance(st, (ast.Assign, ast.AnnAssign)) and isinstance(st.value, ast.Dict):
                tg = st.targets if isinstance(st, ast.Assign) else [st.target]
                if len(tg) != 1 or not isinstance(tg[0], ast.Name): continue
                name = f"{modname}:{'.'.join(qual)}.{tg[0].id}"
                for k in st.value.keys:
                    if k is None: continue
                    try: KEYEXPR[(name, ast.unparse(k))] = K.kr(eval(compile(ast.Expression(k), '<k>', 'eval'), g))
                    except Exception: pass
    walk(tree.body, [])
for m, mo in list(sys.modules.items()):
    if m.startswith('sqlglot') and getattr(mo, '__file__', None): scan(m, mo.__file__)
K.READS.clear()
res = {}; unknown = set(); running = {}
def child(t, w):
    K.READS.clear()
    f, cls, meth = t['id'].split('::'); modname = f[:-3].replace('/', '.')
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.setitimer(signal.ITIMER_REAL, 180)
    ok = False
    try:
        mod = importlib.import_module(modname)
        r = unittest.TestResult()
        unittest.TestSuite([unittest.TestLoader().loadTestsFromName(f'{cls}.{meth}', mod)]).run(r)
        ok = r.wasSuccessful() and r.testsRun == 1 and not r.skipped
        skipped = bool(r.skipped)
    except BaseException:
        skipped = False
    signal.setitimer(signal.ITIMER_REAL, 0)
    K.ON[0] = False
    data = pickle.dumps(('pass' if ok else ('skip' if skipped else 'fail'), frozenset(K.READS)))
    with os.fdopen(w, 'wb') as fh: fh.write(data)
    os._exit(0)
import threading
bufs = {}
def reader(pid, r):
    chunks = []
    with os.fdopen(r, 'rb') as fh: bufs[pid] = fh.read()
threads = []
def reap():
    pid, st = os.wait()
    n = running.pop(pid)
    for th in threads:
        if th.name == str(pid): th.join()
    b = bufs.pop(pid, b'')
    try:
        status, reads = pickle.loads(b)
    except Exception:
        status, reads = 'crash', frozenset()
    res[n] = reads
    if status != 'pass': unknown.add(n)
t0 = time.time()
for t in tests:
    while len(running) >= W: reap()
    r, w = os.pipe(); pid = os.fork()
    if pid == 0:
        os.close(r); dn = os.open(os.devnull, os.O_WRONLY); os.dup2(dn, 1); os.dup2(dn, 2)
        try: child(t, w)
        finally: os._exit(3)
    os.close(w); running[pid] = t['number']
    th = threading.Thread(target=reader, args=(pid, r), name=str(pid), daemon=True); th.start(); threads.append(th)
while running: reap()
pickle.dump({'dicts': K.DICTS, 'import': IMPORT, 'tests': res, 'unknown': unknown, 'keyexpr': KEYEXPR}, open(out, 'wb'))
print('done', len(res), 'unknown', len(unknown), sorted(unknown)[:30], 'dicts', len(K.DICTS), 'keyexpr', len(KEYEXPR), f'{time.time()-t0:.0f}s')
