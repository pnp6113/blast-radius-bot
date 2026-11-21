"""PROTOTYPE runtime probe: run candidate tests against a patched sqlglot in a forked child, without
writing to disk (patched modules are served from memory by a meta-path finder; patched data files via an
open() redirect). Reports which candidate tests fail. Measures CPU. Research prototype, not production.
Usage: python probe.py <sqlglot_dir> <patch> <test_id> [<test_id> ...]"""
import os, sys, io, json, time, resource, signal, builtins, importlib.abc, importlib.util, pickle, unittest
SRC = os.path.abspath(sys.argv[1]) if __name__ == '__main__' else None
os.environ.setdefault('SKIP_INTEGRATION', '0'); os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')

def warm():
    # heavy third-party imports that tests need, done once in the parent (not sqlglot itself)
    import duckdb, pandas, numpy, pandas.testing, concurrent.futures, multiprocessing, decimal, sqlite3, tracemalloc  # noqa

class Overlay(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, mods): self.mods = mods  # modname -> (path, source)
    def find_spec(self, name, path, target=None):
        if name in self.mods:
            p, _ = self.mods[name]
            return importlib.util.spec_from_file_location(name, p, loader=self, submodule_search_locations=[os.path.dirname(p)] if p.endswith('__init__.py') else None)
    def create_module(self, spec): return None
    def exec_module(self, module):
        p, s = self.mods[module.__name__]
        exec(compile(s, p, 'exec'), module.__dict__)

def sync_pool():
    import concurrent.futures as cf, concurrent.futures.process as cfp
    class SyncExecutor(cf.Executor):
        def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kw):
            if initializer: initializer(*pickle.loads(pickle.dumps(initargs)))
        def submit(self, fn, /, *a, **k):
            f = cf.Future()
            try:
                a, k = pickle.loads(pickle.dumps((a, k)))
                f.set_result(pickle.loads(pickle.dumps(fn(*a, **k))))
            except BaseException as e: f.set_exception(e)
            return f
        def shutdown(self, wait=True, *, cancel_futures=False): pass
    cf.ProcessPoolExecutor = cfp.ProcessPoolExecutor = SyncExecutor

class Rec(unittest.TestResult):
    def __init__(self): super().__init__(); self.bad = set()
    def addFailure(self, t, e): self.bad.add(t.id())
    def addError(self, t, e): self.bad.add(getattr(t, 'id', lambda: str(t))())
    def addSubTest(self, t, st, e):
        if e is not None: self.bad.add(t.id())
    def addUnexpectedSuccess(self, t): self.bad.add(t.id())

def child(src, files, test_ids, per_test_timeout, wfd):
    os.chdir(src); sys.path.insert(0, src)
    py = {}; data = {}
    for rel, text in files.items():
        ap = os.path.join(src, rel)
        if rel.endswith('.py'):
            mod = rel[:-3].replace('/', '.').removesuffix('.__init__')
            py[mod] = (ap, text)
        else: data[ap] = text
    sys.meta_path.insert(0, Overlay(py))
    if data:
        real_open = builtins.open
        def fake_open(file, mode='r', *a, **k):
            if isinstance(file, (str, os.PathLike)) and os.path.abspath(os.fspath(file)) in data and 'r' in mode:
                t = data[os.path.abspath(os.fspath(file))]
                return io.BytesIO(t.encode()) if 'b' in mode else io.StringIO(t)
            return real_open(file, mode, *a, **k)
        builtins.open = fake_open; io.open = fake_open
    sync_pool()
    res = {}
    loader = unittest.TestLoader()
    groups = {}
    for tid in test_ids:
        f, cls, meth = tid.split('::'); groups.setdefault((f, cls), []).append(meth)
    def alarm(*_): raise TimeoutError('probe timeout')
    signal.signal(signal.SIGALRM, alarm)
    for (f, cls), meths in groups.items():
        modname = f[:-3].replace('/', '.')
        ids = [f'{f}::{cls}::{m}' for m in meths]
        r = Rec()
        signal.setitimer(signal.ITIMER_REAL, per_test_timeout * len(meths))
        try:
            mod = importlib.import_module(modname)
            suite = unittest.TestSuite([loader.loadTestsFromName(f'{cls}.{m}', mod) for m in meths])
            suite.run(r)   # one suite per class => setUpClass runs once
            bad_class = any('setUpClass' in str(t) or 'tearDownClass' in str(t) for t, _ in r.errors)
            done = {t.id().split('.')[-1] for t in getattr(r, '_ran', [])}
            for m, i in zip(meths, ids):
                tid_dot = f'{modname}.{cls}.{m}'
                res[i] = 'fail' if (bad_class or tid_dot in r.bad or any(tid_dot == getattr(t, 'id', lambda: '')() for t, _ in r.errors + r.failures)) else 'pass'
        except BaseException:
            for i in ids: res[i] = 'fail'
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    os.write(wfd, json.dumps(res).encode()); os.close(wfd); os._exit(0)

def probe(src, files, test_ids, per_test_timeout=30.0):
    src = os.path.abspath(src)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        devnull = os.open(os.devnull, os.O_WRONLY); os.dup2(devnull, 1); os.dup2(devnull, 2)
        try: child(src, files, test_ids, per_test_timeout, w)
        finally: os._exit(3)
    os.close(w)
    buf = b''
    while True:
        b = os.read(r, 1 << 16)
        if not b: break
        buf += b
    os.waitpid(pid, 0)
    try: return json.loads(buf)
    except Exception: return {t: 'fail' for t in test_ids}  # crash => treat as failing (conservative)
