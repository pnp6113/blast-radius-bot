"""Import-time change analysis (prototype). For a changed line in a module/class body (executed at import),
find runtime read sites of the assigned names, follow import-time propagation, map to covering tests, and
narrow by dialect usage. Returns (tests, info)."""
import ast, os, re, json
from pathlib import Path
from functools import lru_cache
DIALECT_DIRS = ('sqlglot/dialects/', 'sqlglot/parsers/', 'sqlglot/generators/', 'sqlglot/typing/', 'sqlglot/tokenizers/')

class Index:
    def __init__(self, src, cov):
        self.src = Path(src); self.cov = cov
        self.reads = {}       # name -> list of (file, line, runtime:bool, enclosing assigned names if import-time)
        self.rtown = {}       # runtime read -> names assigned by its enclosing statement (for import-executed derivations)
        self._rtown = None
        self.keyuse = set()   # runtime reads that only observe keys/truthiness
        self.stores = set()   # store-context attribute entries in reads (kept for the old behaviour, skipped downstream)
        self.xfer = {}        # (file, line, name, idx) -> names a runtime read's value is transferred into, or None
        for p in (self.src / 'sqlglot').rglob('*.py'):
            rel = str(p.relative_to(self.src)); tree = ast.parse(p.read_text())
            self._rel = rel; self._fspan = None
            self._walk(rel, tree, runtime=False, owner=None)
        self._dialect_users = {}
        self.funcs = {}; self.classes = {}
        for p in (self.src / 'sqlglot').rglob('*.py'):
            rel = str(p.relative_to(self.src))
            for n in ast.walk(ast.parse(p.read_text())):
                if isinstance(n, ast.ClassDef):
                    self.classes.setdefault(n.name, []).append((rel, (n.lineno, n.end_lineno)))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.funcs.setdefault(n.name, []).append((rel, (n.lineno, n.end_lineno)))
    def _xfer(self, node, ch):
        # value transfer: `kw=<ch>` in a call, or `<target> = <ch>` -> names the value flows into
        if isinstance(node, ast.keyword) and node.value is ch and node.arg: return (node.arg,)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is ch:
            tg = node.targets if isinstance(node, ast.Assign) else [node.target]
            out = []
            for t in tg:
                if isinstance(t, ast.Name): out.append(('L', t.id, self._rel, self._fspan) if self._fspan else t.id)
                elif isinstance(t, ast.Attribute): out.append(t.attr)
                else: return None
            return tuple(out)
        return None
    @staticmethod
    def _truthctx(p, ch):
        return (isinstance(p, (ast.If, ast.While, ast.IfExp, ast.Assert)) and p.test is ch) or \
               (isinstance(p, ast.UnaryOp) and isinstance(p.op, ast.Not)) or \
               (isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and p.func.id in ('bool', 'len') and len(p.args) == 1 and p.args[0] is ch)
    def _keyuse(self, node, ch, gp):
        # the read only observes the KEYS / truthiness of the value (so a dict-values-only edit can't be seen here)
        if isinstance(node, ast.Compare) and any(c is ch for c in node.comparators) and all(isinstance(o, (ast.In, ast.NotIn)) for o in node.ops): return True
        if self._truthctx(node, ch): return True
        if isinstance(node, ast.BoolOp) and gp is not None and self._truthctx(gp, node): return True
        return False
    def _walk(self, rel, node, runtime, owner, gp=None):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                # decorators/defaults run at import; body at runtime
                prev = self._fspan
                if not isinstance(ch, ast.Lambda): self._fspan = (ch.lineno, ch.end_lineno)
                self._walk(rel, ch, True, None, node)
                self._fspan = prev
                continue
            own = owner
            if not runtime and isinstance(ch, ast.stmt) and not isinstance(ch, ast.ClassDef):
                own = tuple(sorted(assigned_names(ch)))
            if runtime and isinstance(ch, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                tg = ch.targets if isinstance(ch, ast.Assign) else [ch.target]
                self._rtown = tuple(sorted({t.attr for t in tg if isinstance(t, ast.Attribute)} | {n.id for t in tg for n in ast.walk(t) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}))
            elif runtime and isinstance(ch, ast.stmt):
                self._rtown = None
            if isinstance(ch, ast.Attribute) and isinstance(ch.ctx, ast.Load):
                self.reads.setdefault(ch.attr, []).append((rel, ch.lineno, runtime, own)); self.xfer[(rel, ch.lineno, ch.attr, len(self.reads[ch.attr]) - 1)] = self._xfer(node, ch) if runtime else None
                if runtime and self._rtown: self.rtown[(rel, ch.lineno, ch.attr, len(self.reads[ch.attr]) - 1)] = self._rtown
                if runtime and self._keyuse(node, ch, gp): self.keyuse.add((rel, ch.lineno, ch.attr, len(self.reads[ch.attr]) - 1))
            elif isinstance(ch, ast.Attribute):
                self.reads.setdefault(ch.attr, []).append((rel, ch.lineno, runtime, own)); self.stores.add((rel, ch.lineno, ch.attr, len(self.reads[ch.attr]) - 1))
            elif isinstance(ch, ast.Name) and isinstance(ch.ctx, ast.Load):
                self.reads.setdefault(ch.id, []).append((rel, ch.lineno, runtime, own)); self.xfer[(rel, ch.lineno, ch.id, len(self.reads[ch.id]) - 1)] = self._xfer(node, ch) if runtime else None
                if runtime and self._rtown: self.rtown[(rel, ch.lineno, ch.id, len(self.reads[ch.id]) - 1)] = self._rtown
                if runtime and self._keyuse(node, ch, gp): self.keyuse.add((rel, ch.lineno, ch.id, len(self.reads[ch.id]) - 1))
            elif isinstance(ch, ast.Constant) and isinstance(ch.value, str) and ch.value.isidentifier():
                self.reads.setdefault(ch.value, []).append((rel, ch.lineno, runtime, own))  # getattr(x, "NAME")
            self._walk(rel, ch, runtime, own, node)
    def tests_at(self, rel, line):
        return set(self.cov.get(rel, {}).get(str(line), []))
    def dialect_users(self, stem):
        if stem not in self._dialect_users:
            s = set()
            if not hasattr(self, '_dmap'):
                p = Path(os.environ.get('BR_ARTIFACTS', Path(__file__).resolve().parents[1] / 'artifacts')) / 'dialect_usage_map.json'
                self._dmap = json.load(open(p)) if p.exists() else {}
            for k, v in self._dmap.items():
                if k.startswith(f'sqlglot.dialects.{stem}.'): s.update(v)
            for d in DIALECT_DIRS:
                for l, ts in self.cov.get(f'{d}{stem}.py', {}).items(): s.update(ts)
            self._dialect_users[stem] = s
        return self._dialect_users[stem]

def assigned_names(stmt):
    out = set()
    for n in ast.walk(stmt):
        if isinstance(n, (ast.Name)) and isinstance(n.ctx, ast.Store): out.add(n.id)
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)): out.add(n.name)
    return out

def subclasses_stems(src, stem):
    """Dialects that inherit (by import) from the given dialect file stem, e.g. tsql -> fabric."""
    out = {stem}
    changed = True
    while changed:
        changed = False
        for p in (Path(src) / 'sqlglot/dialects').glob('*.py'):
            t = p.read_text()
            if p.stem not in out and any(re.search(rf'from sqlglot\.dialects\.{s} import', t) for s in out):
                out.add(p.stem); changed = True
    return out

def class_users(idx, cname):
    s = set()
    for f, l, runtime, own in idx.reads.get(cname, []):
        if runtime: s |= idx.tests_at(f, l)
    for f, l, runtime, own in idx.reads.get(cname.lower() + '_sql', []):
        s |= idx.tests_at(f, l)
    for f, (a, b) in idx.classes.get(cname, []):   # any covered line inside the class body (its methods)
        for line in range(a, b + 1): s |= idx.tests_at(f, line)
    for f, d in idx.funcs.get(cname.lower() + '_sql', []):
        for line in range(d[0], d[1] + 1): s |= idx.tests_at(f, line)
    return s

DEFUSE = os.environ.get('BR_DEFUSE', '0') == '1'
IMPDERIV = os.environ.get('BR_IMPDERIV', '0') == '1'   # follow values derived inside functions that run at import
INSTMAP = os.environ.get('BR_INSTMAP', '0') == '1'     # class filter also uses the runtime instantiation map
RT_DEPTH = 6
def _readers(idx, tok):
    if isinstance(tok, tuple):   # ('L', name, file, (start, end)) local variable: same function only
        _, nm, rel, (a, b) = tok
        return [(i, r) for i, r in enumerate(idx.reads.get(nm, [])) if r[0] == rel and a <= r[1] <= b], nm
    return list(enumerate(idx.reads.get(tok, []))), tok

_INST = None
def inst_map():
    global _INST
    if _INST is None:
        p = os.environ.get('BR_INSTMAPF', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'class_inst_map.json'))
        d = json.load(open(p)); _INST = {'users': d['users'], 'import_instantiated': set(d['import_instantiated'])}
    return _INST

_IMPL = None
def import_line(f, l):
    """Line executed while importing every sqlglot + test module (build-time coverage of import_all.py)."""
    global _IMPL
    if _IMPL is None:
        p = os.environ.get('BR_IMPORTLINES', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'import_lines.json'))
        _IMPL = {k: set(v) for k, v in json.load(open(p)).items()}
    return l in _IMPL.get(f, ())

_IMPCTX = None
def import_executed(f, l):
    """Line executed outside any test (import/collection) in the pinned-commit coverage run."""
    global _IMPCTX
    if _IMPCTX is None:
        p = os.environ.get('BR_IMPORTCTX', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'importctx_lines.json'))
        _IMPCTX = {k: set(v) for k, v in json.load(open(p)).items()}
    return l in _IMPCTX.get(f, ())

def reach(idx, toks, vo=False, cache={}):
    """Union of tests covering every consumption site reachable from `toks` through runtime value transfers
    (kwarg passing / plain assignment), by name. None = unknown (a reached token has no runtime readers, or the
    depth limit cut the chain) -> caller falls back to its own site's tests."""
    key = (tuple(sorted(map(str, toks))), vo)
    if key in cache: return cache[key]
    out = set(); seen = set(); frontier = list(toks); depth = 0; unknown = False
    while frontier and not unknown:
        if depth >= RT_DEPTH: unknown = True; break
        nxt = []
        for tok in frontier:
            if tok in seen: continue
            seen.add(tok)
            rs, nm = _readers(idx, tok); any_rt = False
            for i, (f, l, runtime, own) in rs:
                k = (f, l, nm, i)
                if not runtime or k in idx.stores: continue
                any_rt = True
                if vo and k in idx.keyuse: continue
                xf = idx.xfer.get(k)
                if xf: nxt.extend(xf)
                elif import_executed(f, l): unknown = True; break   # value may be consumed at import; coverage can't see it
                else: out |= idx.tests_at(f, l)
            if unknown: break
            if not any_rt: unknown = True; break
        frontier = nxt; depth += 1
    cache[key] = None if unknown else out
    return cache[key]

def follow_runtime(idx, seeds, vo=False):
    out = set()
    for xf, site in seeds:
        d = reach(idx, xf, vo)
        out |= site if d is None else (site & d)
    return out

def analyze(idx, rel, stmt_node, max_depth=3, enclosing_class=None, values_only=False):
    names = set(assigned_names(stmt_node))
    # dict literal keys inside the statement are also "names" read via lookups
    tests = set(); seen = set(); frontier = set(names); depth = 0
    rt_seed = []   # (name, fallback_tests) for runtime transfers
    while frontier and depth < max_depth:
        nxt = set()
        for nm in frontier - seen:
            seen.add(nm)
            for i, (f, l, runtime, own) in enumerate(idx.reads.get(nm, [])):
                if runtime:
                    if values_only and depth == 0:
                        if (f, l, nm, i) in idx.keyuse: continue
                        if import_executed(f, l) and not (DEFUSE and idx.xfer.get((f, l, nm, i))):   # value consumed at import: refinement unsafe
                            return analyze(idx, rel, stmt_node, max_depth, enclosing_class, False)
                    if IMPDERIV and import_line(f, l) and (f, l, nm, i) in idx.rtown:
                        nxt |= set(idx.rtown[(f, l, nm, i)]) - {'self', 'cls'}   # derived at import (e.g. metaclass) -> follow
                    xf = idx.xfer.get((f, l, nm, i)) if DEFUSE else None
                    if xf: rt_seed.append((xf, idx.tests_at(f, l)))
                    else: tests |= idx.tests_at(f, l)
                elif own: nxt |= set(own)
        frontier = nxt; depth += 1
    if rt_seed: tests |= follow_runtime(idx, rt_seed, values_only)
    info = {'names': sorted(names), 'followed': sorted(seen)}
    if rel.startswith(DIALECT_DIRS):
        stem = Path(rel).stem
        users = set()
        for s in subclasses_stems(idx.src, stem): users |= idx.dialect_users(s)
        info['dialect_filter'] = len(users)
        if not rel.startswith('sqlglot/dialects/dialect.py'): tests &= users
    elif enclosing_class:
        cu = class_users(idx, enclosing_class)
        if INSTMAP:
            im = inst_map()
            if enclosing_class in im['import_instantiated']: cu = set()      # instantiated at import: no filter
            else: cu = cu | set(im['users'].get(enclosing_class, []))
        info['class_filter'] = (enclosing_class, len(cu))
        if cu: tests &= cu
    return tests, info

def values_only_change(old, new):
    """True if old/new statements are the same dict-literal assignment differing only in values (same keys, order)."""
    if type(old) is not type(new) or not isinstance(old, (ast.Assign, ast.AnnAssign)): return False
    to = old.targets if isinstance(old, ast.Assign) else [old.target]; tn = new.targets if isinstance(new, ast.Assign) else [new.target]
    if [ast.dump(t) for t in to] != [ast.dump(t) for t in tn]: return False
    if not (isinstance(old.value, ast.Dict) and isinstance(new.value, ast.Dict)): return False
    if len(old.value.keys) != len(new.value.keys) or any(k is None for k in old.value.keys + new.value.keys): return False
    return [ast.dump(k) for k in old.value.keys] == [ast.dump(k) for k in new.value.keys]
