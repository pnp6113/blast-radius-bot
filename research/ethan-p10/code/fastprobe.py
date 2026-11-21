"""Warm-parent probe engine (opt/). The parent imports sqlglot + the needed test modules ONCE; each probe forks a
child that applies the patch in memory and runs the requested tests with unittest.

Patch application in the child, per changed file:
  * sqlglot .py module already imported in the parent, change confined to function bodies (stripped-AST equal),
    no changed function executed while the parent was importing  -> HOT SWAP: replace __code__ (and __doc__) of
    every live function object built from a changed code object.
  * sqlglot .py module not imported in the parent -> meta-path overlay serves the patched source when imported.
  * anything else (import-time change, function that ran during import, ambiguous live closures, data file
    read during import, syntax error, ...) -> COLD: purge sqlglot*/tests* from sys.modules in the child and
    re-import everything through the overlay (same as the old probe.py, minus 3rd-party imports).
Data files (fixtures) are served through an open() redirect.
Research prototype; no disk writes."""
import os, sys, io, ast, gc, json, signal, builtins, importlib, importlib.abc, importlib.util, pickle, unittest, collections

os.environ.setdefault('SKIP_INTEGRATION', '0'); os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
SRC = None
IMPORT_EXEC = set()     # (filename, qualname, firstlineno) of code run during parent imports
IMPORT_OPENED = set()   # abs paths opened during parent imports
_FUNCIDX = None
STATS = collections.Counter()

PRESET = {}   # (modname, clsname) -> (exec set, opened set)  setUpClass already run in the parent
def _globals_snapshot():
    snap = {}
    for m, mod in list(sys.modules.items()):
        if (m == 'sqlglot' or m.startswith('sqlglot.') or m.startswith('tests')) and mod is not None:
            snap[m] = dict(vars(mod))
    return snap
def _restore(before):
    for m, d in before.items():
        mod = sys.modules.get(m)
        if mod is None: continue
        cur = vars(mod)
        for k in list(cur):
            if k not in d: del cur[k]
        for k, v in d.items():
            if cur.get(k, object()) is not v: cur[k] = v
def _same(a, b):
    if a.keys() != b.keys(): return False
    return all(a[m].keys() == b[m].keys() and all(a[m][k] is b[m][k] for k in a[m]) for m in a)

class _RecConn:
    """duckdb connection made during a preset setUpClass: calls are recorded, the real connection is closed
    before forking (duckdb threads are not fork-safe) and lazily rebuilt + replayed on first use in a child."""
    def __init__(self, connect, a, k):
        self._c = connect; self._ak = (a, k); self._log = []; self._real = connect(*a, **k); self._rec = True
    def _live(self):
        if self._real is None:
            self._real = self._c(*self._ak[0], **self._ak[1])
            for n, a, k in self._log: getattr(self._real, n)(*a, **k)
        return self._real
    def _park(self):
        self._rec = False
        if self._real is not None: self._real.close(); self._real = None
    def __getattr__(self, name):
        if name.startswith('_'): raise AttributeError(name)
        real = self._live(); attr = getattr(real, name)
        if not callable(attr) or not self._rec: return attr
        def f(*a, **k):
            r = attr(*a, **k); self._log.append((name, a, k)); return r
        return f
    def __enter__(self): return self
    def __exit__(self, *e): return False

def _nthreads():
    try: return len(os.listdir('/proc/self/task'))
    except Exception: return 1

def preset(classes, min_cpu=0.2):
    """Run setUpClass once in the parent for expensive classes. Records sqlglot code executed and files opened
    during setup; the child reuses the class state only when the patch touches none of them. Classes whose setup
    rebinds any sqlglot/tests module global are rejected and rolled back (state could leak between classes)."""
    import time
    mon = sys.monitoring; TID = 4
    dd = sys.modules.get('duckdb'); conns = []
    if dd is not None:
        real_connect = dd.connect
        def rec_connect(*a, **k):
            c = _RecConn(real_connect, a, k); conns.append(c); return c
    base_threads = _nthreads()
    for modname, clsname in classes:
        try: C = getattr(importlib.import_module(modname), clsname)
        except Exception: continue
        if 'setUpClass' not in vars(C): continue
        ex, op = set(), set()
        mon.use_tool_id(TID, 'br-preset')
        def on_start(code, off):
            if code.co_filename.startswith(SRC): ex.add((code.co_filename, code.co_qualname, code.co_firstlineno))
            return mon.DISABLE
        mon.register_callback(TID, mon.events.PY_START, on_start); mon.set_events(TID, mon.events.PY_START)
        _OPEN_REC[0] = op
        before = _globals_snapshot(); n0 = len(conns); t = time.process_time(); ok = True
        cls_before = dict(vars(C))
        if dd is not None: dd.connect = rec_connect
        try: C.setUpClass()
        except BaseException: ok = False
        finally:
            if dd is not None: dd.connect = real_connect
            _OPEN_REC[0] = None
            mon.set_events(TID, 0); mon.register_callback(TID, mon.events.PY_START, None); mon.free_tool_id(TID)
        dt = time.process_time() - t
        if ok and _same(_globals_snapshot(), before) and dt >= min_cpu:
            PRESET[(modname, clsname)] = (ex, op)
            SNAP[(modname, clsname)] = ({k: v for k, v in vars(C).items() if cls_before.get(k, _MISSING) is not v}, conns[n0:])
        else:
            try: C.doClassCleanups(); C.tearDownClass()
            except BaseException: pass
            _restore(before)
            for c in conns[n0:]: c._park()
            del conns[n0:]
    for c in conns: c._park()
    gc.collect()
    if _nthreads() > base_threads:   # something left threads behind: forking would be unsafe -> drop presets
        PRESET.clear(); STATS['preset_dropped'] += 1
    gc.freeze()

_OPEN_REC = [None]
_MISSING = object()
SNAP = {}

def save_presets(path):
    """Build time: pickle the class state produced by preset() (paths relative to SRC)."""
    out = {}
    for key, (attrs, cs) in SNAP.items():
        ex, op = PRESET[key]; enc = {}
        try:
            for k, v in attrs.items():
                if isinstance(v, _RecConn): enc[k] = ('conn', v._ak, v._log)
                else: enc[k] = ('val', pickle.dumps(v, protocol=5))
        except Exception:
            continue
        rel = lambda p: os.path.relpath(p, SRC)
        out[key] = {'attrs': enc, 'exec': sorted((rel(f), q, l) for f, q, l in ex), 'opened': sorted(rel(p) for p in op)}
    with open(path, 'wb') as fh: pickle.dump(out, fh, protocol=5)
    return list(out)

def load_presets(path, classes):
    """Run time: restore pickled setUpClass state for the needed classes instead of running setUpClass."""
    try:
        with open(path, 'rb') as fh: data = pickle.load(fh)
    except Exception: return
    dd = None
    for key in classes:
        d = data.get(tuple(key))
        if d is None: continue
        try:
            C = getattr(importlib.import_module(key[0]), key[1])
            vals = {}
            for k, (kind, *rest) in d['attrs'].items():
                if kind == 'conn':
                    if dd is None: import duckdb as dd
                    c = _RecConn.__new__(_RecConn); c._c = dd.connect; c._ak = rest[0]; c._log = rest[1]; c._real = None; c._rec = False
                    vals[k] = c
                else: vals[k] = pickle.loads(rest[0])
        except Exception:
            continue
        for k, v in vals.items(): setattr(C, k, v)
        ab = lambda p: os.path.join(SRC, p)
        PRESET[tuple(key)] = ({(ab(f), q, l) for f, q, l in d['exec']}, {ab(p) for p in d['opened']})
    gc.collect(); gc.freeze()
def _open_hook(ev, args):
    if _OPEN_REC[0] is not None and ev == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        try: _OPEN_REC[0].add(os.path.abspath(os.fsdecode(args[0])))
        except Exception: pass

def warm(src, test_modules, third_party=()):
    """Import sqlglot + given test modules once, recording which code runs at import time."""
    global SRC
    SRC = os.path.abspath(src)
    if SRC not in sys.path: sys.path.insert(0, SRC)
    os.chdir(SRC)
    mon = sys.monitoring; TID = 3
    mon.use_tool_id(TID, 'br-importexec')
    def on_start(code, off):
        if code.co_filename.startswith(SRC): IMPORT_EXEC.add((code.co_filename, code.co_qualname, code.co_firstlineno))
        return mon.DISABLE
    mon.register_callback(TID, mon.events.PY_START, on_start)
    mon.set_events(TID, mon.events.PY_START)
    rec = [True]
    def hook(ev, args):
        if rec[0] and ev == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            try: IMPORT_OPENED.add(os.path.abspath(os.fsdecode(args[0])))
            except Exception: pass
    sys.addaudithook(hook); sys.addaudithook(_open_hook)
    sync_pool()   # MUST precede test imports: tests do `from concurrent.futures import ProcessPoolExecutor`
    try:
        for m in third_party: importlib.import_module(m)
        import sqlglot  # noqa
        for m in test_modules:
            try: importlib.import_module(m)
            except Exception: pass
    finally:
        rec[0] = False
        mon.set_events(TID, 0); mon.register_callback(TID, mon.events.PY_START, None); mon.free_tool_id(TID)
    gc.collect(); _funcidx(); gc.freeze()   # index BEFORE freeze: gc.get_objects() skips frozen objects

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

class Overlay(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, mods): self.mods = mods
    def find_spec(self, name, path, target=None):
        if name in self.mods:
            p, _ = self.mods[name]
            return importlib.util.spec_from_file_location(name, p, loader=self, submodule_search_locations=[os.path.dirname(p)] if p.endswith('__init__.py') else None)
    def create_module(self, spec): return None
    def exec_module(self, module):
        p, s = self.mods[module.__name__]
        exec(compile(s, p, 'exec'), module.__dict__)

# ---------- hot-swap planning ----------
class _Strip(ast.NodeTransformer):
    def visit_FunctionDef(self, n):
        n.decorator_list = [self.visit(d) for d in n.decorator_list]; n.args = self.visit(n.args)
        n.body = [ast.Pass()]; return n
    visit_AsyncFunctionDef = visit_FunctionDef
    def visit_Lambda(self, n):
        n.args = self.visit(n.args); n.body = ast.Constant(None); return n

def _stripped(tree): return ast.dump(_Strip().visit(tree), include_attributes=False)

def _codes(co, acc):
    for k in co.co_consts:
        if hasattr(k, 'co_code'): acc.setdefault(k.co_qualname, []).append(k); _codes(k, acc)
    return acc

def _sig(co):
    consts = tuple(('<code>', _sig(k)) if hasattr(k, 'co_code') else (type(k).__name__, repr(k)) for k in co.co_consts)
    return (co.co_code, consts, co.co_names, co.co_varnames, co.co_freevars, co.co_cellvars, co.co_flags,
            co.co_argcount, co.co_kwonlyargcount, co.co_posonlyargcount)

def plan_swap(path, old_src, new_src):
    """Return list of (qualname, old_firstlineno, new_code, new_doc_or_None) or a string reason for COLD."""
    import astcache as AC
    try: AC.parse(new_src)
    except SyntaxError: return 'syntax'
    if not AC.same_sdump(old_src, new_src): return 'import-time change'
    oc = _codes(compile(old_src, path, 'exec'), {}); nc = _codes(compile(new_src, path, 'exec'), {})
    for q, cs in oc.items():   # two code objects with the same qualname on the same first line cannot be told apart
        fl = [c.co_firstlineno for c in cs]
        if len(fl) != len(set(fl)): 
            if [c.co_firstlineno for c in nc.get(q, [])] != fl or any(_sig(a) != _sig(b) for a, b in zip(cs, nc.get(q, []))): return f'ambiguous {q}'
    swaps = []
    for q in set(oc) | set(nc):
        o, n = oc.get(q, []), nc.get(q, [])
        if len(o) == len(n):
            for a, b in zip(o, n):
                if not (a.co_flags & 0x2):   # not a function (class body): stripped-AST equality already covers it
                    continue
                if _sig(a) != _sig(b):
                    if (path, q, a.co_firstlineno) in IMPORT_EXEC: return f'ran at import: {q}'
                    if a.co_freevars != b.co_freevars: return f'freevars: {q}'
                    swaps.append((q, a.co_firstlineno, b))
        else:
            # nested code objects whose count changed: only OK if no live function carries them
            swaps.append((q, None, None))
    return swaps

_LRU = {}
def _funcidx():
    global _FUNCIDX
    if _FUNCIDX is None:
        import types, functools
        _FUNCIDX = collections.defaultdict(list)
        for o in gc.get_objects():
            if type(o) is types.FunctionType:
                c = o.__code__
                if c.co_filename.startswith(SRC): _FUNCIDX[(c.co_filename, c.co_qualname)].append(o)
            elif isinstance(o, functools._lru_cache_wrapper):
                _LRU.setdefault(id(getattr(o, '__wrapped__', None)), []).append(o)
    return _FUNCIDX

def _docstr(code):
    return code.co_consts[0] if code.co_consts and isinstance(code.co_consts[0], str) else None

# ---------- child ----------
class Rec(unittest.TestResult):
    def __init__(self, stop_on_fail=False): super().__init__(); self.bad = set(); self.sof = stop_on_fail
    def _b(self, tid):
        self.bad.add(tid)
        if self.sof: self.stop()
    def addFailure(self, t, e): self._b(t.id())
    def addError(self, t, e): self._b(getattr(t, 'id', lambda: str(t))())
    def addSubTest(self, t, st, e):
        if e is not None: self._b(t.id())
    def addUnexpectedSuccess(self, t): self._b(t.id())

def _adump(node):
    if isinstance(node, ast.AST):
        return ast.dump(node, include_attributes=False)
    if isinstance(node, list):
        return "[" + ",".join(_adump(item) for item in node) + "]"
    return repr(node)


def _literal_updates(old_src, new_src, rel):
    """Class/module assigns whose only change is a literal, or None if the edit is wider.

    A plain class (metaclass type) reads these attributes at call time. Expression's metaclass
    copies its dict into the class object at creation, so those stay on the cold path.
    """
    try:
        old_tree, new_tree = ast.parse(old_src), ast.parse(new_src)
    except SyntaxError:
        return None
    old_lines, new_lines = old_src.splitlines(), new_src.splitlines()
    if len(old_lines) != len(new_lines):
        return None
    spans = [
        (node.lineno, node.end_lineno)
        for node in ast.walk(old_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and node.end_lineno
    ]
    for lineno, (before, after) in enumerate(zip(old_lines, new_lines), start=1):
        if before != after and any(start <= lineno <= end for start, end in spans):
            return None
    updates = []

    def walk(old_body, new_body, qual):
        if len(old_body) != len(new_body):
            return False
        for left, right in zip(old_body, new_body):
            if isinstance(left, ast.ClassDef) or isinstance(right, ast.ClassDef):
                if type(left) is not type(right) or left.name != right.name:
                    return False
                if _adump(left.bases) != _adump(right.bases):
                    return False
                nested = f"{qual}.{left.name}" if qual else left.name
                if not walk(left.body, right.body, nested):
                    return False
                continue
            if isinstance(left, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                # Body text is compared once, below. Dumping every function is slower than the probe it saves.
                if left.name != right.name or left.lineno != right.lineno or left.end_lineno != right.end_lineno:
                    return False
                continue
            if _adump(left) == _adump(right):
                continue
            target = _assigned_name(left)
            if not target or target != _assigned_name(right):
                return False
            value_node = right.value if isinstance(right, (ast.Assign, ast.AnnAssign)) else None
            old_node = left.value if isinstance(left, (ast.Assign, ast.AnnAssign)) else None
            if value_node is None or old_node is None:
                return False
            try:
                value = ast.literal_eval(value_node)
                old_value = ast.literal_eval(old_node)
            except (ValueError, SyntaxError):
                return False
            # Dict key sets are snapshotted when the class is created (escape formats). arg_types is
            # re-derived into required_args in the child, so its keys may change.
            if isinstance(value, dict) or isinstance(old_value, dict):
                if not isinstance(value, dict) or not isinstance(old_value, dict):
                    return False
                if target != "arg_types" and set(value) != set(old_value):
                    return False
            updates.append((rel[:-3].replace("/", ".").removesuffix(".__init__"), qual, target, value))
        return True

    if not walk(old_tree.body, new_tree.body, ""):
        return None
    return updates


def _assigned_name(node):
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _plain_class(mod_name, qual):
    if not qual:
        return True
    mod = sys.modules.get(mod_name)
    if mod is None:
        return False
    obj = mod
    for part in qual.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return False
    return type(obj) is type


def prepare(files):
    """Decide per file how to apply. Returns (mode, detail) mode in {'hot','cold'}. Runs in the parent (cheap)."""
    hot = []; reasons = []; consts = []
    for rel, text in files.items():
        ap = os.path.join(SRC, rel)
        if rel.endswith('.py'):
            if not rel.startswith('sqlglot/'): reasons.append('non-sqlglot py'); continue
            mod = rel[:-3].replace('/', '.').removesuffix('.__init__')
            if mod not in sys.modules: continue          # overlay handles it
            old = open(ap).read()
            pl = plan_swap(ap, old, text)
            if pl == 'import-time change':
                updates = _literal_updates(old, text, rel)
                if updates is not None and all(_plain_class(mod_name, qual) for mod_name, qual, _attr, _value in updates):
                    consts.extend(updates)
                    continue
                reasons.append(pl); continue
            if isinstance(pl, str): reasons.append(pl); continue
            idx = _funcidx()
            for q, l, nc in pl:
                live = idx.get((ap, q), [])
                if nc is None:
                    if live: reasons.append(f'ambiguous live {q}')
                    continue
                hot.append((ap, q, l, nc))
        else:
            if ap in IMPORT_OPENED: reasons.append(f'data read at import: {rel}')
    if reasons: return ('cold', reasons)
    touched = {(ap, q, l) for ap, q, l, _ in hot}
    datas = {os.path.join(SRC, rel) for rel in files if not rel.endswith('.py')}
    newmods = {os.path.join(SRC, rel) for rel in files if rel.endswith('.py')}
    bad = set()
    for k, (ex, op) in PRESET.items():
        if touched & ex or datas & op: bad.add(k)
    return ('hot', hot, bad, consts)

def child(files, test_ids, plan, per_test_timeout, wfd, stop_on_fail):
    gc.enable()
    py = {}; data = {}
    for rel, text in files.items():
        ap = os.path.join(SRC, rel)
        if rel.endswith('.py'): py[rel[:-3].replace('/', '.').removesuffix('.__init__')] = (ap, text)
        else: data[ap] = text
    mode, detail = plan[0], plan[1]
    stale = plan[2] if len(plan) > 2 else set(PRESET)
    consts = plan[3] if len(plan) > 3 else ()
    if mode == 'cold':
        for m in list(sys.modules):
            if m == 'sqlglot' or m.startswith('sqlglot.') or m == 'tests' or m.startswith('tests.'): del sys.modules[m]
        sys.meta_path.insert(0, Overlay(py))
    else:
        sys.meta_path.insert(0, Overlay({m: v for m, v in py.items() if m not in sys.modules}))
        idx = _funcidx(); swapped = set()
        for ap, q, l, nc in detail:
            for f in idx.get((ap, q), []):
                if f.__code__.co_firstlineno == l:
                    f.__code__ = nc
                    d = _docstr(nc)
                    if f.__doc__ != d and (d is not None or f.__doc__ is not None): f.__doc__ = d
                    swapped.add(id(f))
        for i in swapped:   # drop memoised results of swapped functions
            for o in _LRU.get(i, []): o.cache_clear()
        for mod_name, qual, attr, value in consts:
            obj = sys.modules[mod_name]
            for part in filter(None, qual.split(".")):
                obj = getattr(obj, part)
            setattr(obj, attr, value)
            if attr == "arg_types" and isinstance(value, dict):
                required = {key for key, flag in value.items() if flag}
                obj.required_args = required
                pending = list(obj.__subclasses__())
                while pending:
                    sub = pending.pop()
                    pending.extend(sub.__subclasses__())
                    if "arg_types" not in sub.__dict__:
                        sub.required_args = set(required)
    if data:
        real_open = builtins.open
        def fake_open(file, mode='r', *a, **k):
            if isinstance(file, (str, os.PathLike)) and os.path.abspath(os.fspath(file)) in data and 'r' in mode:
                t = data[os.path.abspath(os.fspath(file))]
                return io.BytesIO(t.encode()) if 'b' in mode else io.StringIO(t)
            return real_open(file, mode, *a, **k)
        builtins.open = fake_open; io.open = fake_open
    res = {}; loader = unittest.TestLoader(); groups = {}
    for tid in test_ids:
        f, cls, meth = tid.split('::'); groups.setdefault((f, cls), []).append(meth)
    def alarm(*_): raise TimeoutError('probe timeout')
    signal.signal(signal.SIGALRM, alarm)
    stop = False
    for (f, cls), meths in groups.items():
        modname = f[:-3].replace('/', '.'); ids = [f'{f}::{cls}::{m}' for m in meths]
        if stop:
            for i in ids: res[i] = 'skip'
            continue
        r = Rec(stop_on_fail)
        signal.setitimer(signal.ITIMER_REAL, per_test_timeout * len(meths))
        try:
            mod = importlib.import_module(modname)
            cases = [loader.loadTestsFromName(f'{cls}.{m}', mod) for m in meths]
            if mode == 'hot' and (modname, cls) in PRESET and (modname, cls) not in stale:
                for c in cases:
                    for tc in c: tc.run(r)     # TestCase.run: class setup already done in the parent
            else: unittest.TestSuite(cases).run(r)
            bad_class = any('setUpClass' in str(t) or 'tearDownClass' in str(t) for t, _ in r.errors)
            for m, i in zip(meths, ids):
                tid_dot = f'{modname}.{cls}.{m}'
                if bad_class or tid_dot in r.bad: res[i] = 'fail'
                elif r.shouldStop: res[i] = 'skip'
                else: res[i] = 'pass'
        except BaseException:
            for i in ids: res[i] = 'fail'
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        if stop_on_fail and any(v == 'fail' for v in res.values()): stop = True
    # tests not reported because the suite stopped early count as 'skip' (unknown)
    os.write(wfd, json.dumps(res).encode()); os.close(wfd); os._exit(0)

def probe(files, test_ids, plan=None, per_test_timeout=30.0, stop_on_fail=False):
    """Returns {test_id: 'pass'|'fail'|'skip'}. 'skip' = not run (early stop). Crash => all 'fail'."""
    if plan is None: plan = prepare(files)
    STATS[plan[0]] += 1
    r, w = os.pipe(); pid = os.fork()
    if pid == 0:
        os.close(r)
        dn = os.open(os.devnull, os.O_WRONLY); os.dup2(dn, 1); os.dup2(dn, 2)
        try: child(files, test_ids, plan, per_test_timeout, w, stop_on_fail)
        finally: os._exit(3)
    os.close(w); buf = b''
    while True:
        b = os.read(r, 1 << 16)
        if not b: break
        buf += b
    os.waitpid(pid, 0)
    try: return json.loads(buf)
    except Exception: return {t: 'fail' for t in test_ids}

# ---------- import probe (for import-time changes) ----------
def _import_child(files, modules, wfd):
    gc.enable()
    py = {}
    for rel, text in files.items():
        if rel.endswith('.py'): py[rel[:-3].replace('/', '.').removesuffix('.__init__')] = (os.path.join(SRC, rel), text)
    for m in list(sys.modules):
        if m == 'sqlglot' or m.startswith('sqlglot.') or m == 'tests' or m.startswith('tests.'): del sys.modules[m]
    sys.meta_path.insert(0, Overlay(py))
    bad = []
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    for m in modules:
        signal.setitimer(signal.ITIMER_REAL, 20)
        try: importlib.import_module(m)
        except BaseException: bad.append(m)
        finally: signal.setitimer(signal.ITIMER_REAL, 0)
    os.write(wfd, json.dumps(bad).encode()); os.close(wfd); os._exit(0)

def _import_child_partial(files, modules, wfd):
    gc.enable()
    py = {}
    for rel, text in files.items():
        if rel.endswith('.py'): py[rel[:-3].replace('/', '.').removesuffix('.__init__')] = (os.path.join(SRC, rel), text)
    for m in modules: sys.modules.pop(m, None)
    for m in py: sys.modules.pop(m, None)
    sys.meta_path.insert(0, Overlay(py))
    bad = []
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    for m in modules:
        signal.setitimer(signal.ITIMER_REAL, 20)
        try: importlib.import_module(m)
        except BaseException: bad.append(m)
        finally: signal.setitimer(signal.ITIMER_REAL, 0)
    os.write(wfd, json.dumps(bad).encode()); os.close(wfd); os._exit(0)

def import_probe_partial(files, modules):
    """Like import_probe, but from the warm parent: only `modules` (the reverse import closure of the changed
    modules, computed by the caller) are dropped from sys.modules and re-imported through the overlay."""
    STATS['import_partial'] += 1
    r, w = os.pipe(); pid = os.fork()
    if pid == 0:
        os.close(r)
        dn = os.open(os.devnull, os.O_WRONLY); os.dup2(dn, 1); os.dup2(dn, 2)
        try: _import_child_partial(files, modules, w)
        finally: os._exit(3)
    os.close(w); buf = b''
    while True:
        b = os.read(r, 1 << 16)
        if not b: break
        buf += b
    os.waitpid(pid, 0)
    try: return json.loads(buf)
    except Exception: return list(modules)

def import_probe(files, modules):
    """Fresh-import every module in `modules` against the patched tree; return the ones that fail to import.
    Crash => all modules treated as failing (conservative)."""
    STATS['import'] += 1
    r, w = os.pipe(); pid = os.fork()
    if pid == 0:
        os.close(r)
        dn = os.open(os.devnull, os.O_WRONLY); os.dup2(dn, 1); os.dup2(dn, 2)
        try: _import_child(files, modules, w)
        finally: os._exit(3)
    os.close(w); buf = b''
    while True:
        b = os.read(r, 1 << 16)
        if not b: break
        buf += b
    os.waitpid(pid, 0)
    try: return json.loads(buf)
    except Exception: return list(modules)
