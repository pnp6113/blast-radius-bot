"""RUNTIME: narrow the candidate set of a patch whose ONLY effect is changing entries of class-level dict literals
in sqlglot .py files, using the build-time key-level read index (opt3/keyidx). Returns a set of test numbers, or
None when the patch is out of scope (caller keeps its normal candidates). Conservative by construction:
  * every changed .py file must be AST-identical to the original once class-level dict literals are blanked;
  * per changed dict: no spreads changed, no duplicate keys, unchanged entries keep their relative order,
    the class body (outside defs) never references the attribute name except in its own assignment,
    keys resolve to a recorded key repr (build-time evaluation of the literal's key expressions, or a constant),
    new values are literal-ish (constants/containers/names/attributes/unary/binary ops/lambdas, or calls whose callee
    already appears in the original file);
  * the dict was not read during import for those keys (value change: value reads; add/remove: also membership /
    whole-key ops); any whole-dict mutation or value-exposing op at import -> out of scope;
  * affected tests = tests that looked the key up (+ membership/len/iter for add/remove) + tests that exposed all
    values (items/values/copy/eq/repr) + tests that looked up unclassifiable keys + tests whose tracked build run
    did not pass."""
import ast, os, pickle
HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.environ.get('BR_KEYIDX', os.path.join(HERE, 'keyidx'))
_SH = {}; _META = []
def _shard(mod):
    if mod not in _SH:
        p = os.path.join(IDX, mod + '.pkl')
        _SH[mod] = pickle.load(open(p, 'rb')) if os.path.exists(p) else {}
    return _SH[mod]
def _unknown():
    if not _META: _META.append(pickle.load(open(os.path.join(IDX, '_meta.pkl'), 'rb'))['unknown'])
    return _META[0]
def kr_const(v):
    if v is None: return ('N',)
    if isinstance(v, str): return ('s', v)
    if isinstance(v, bool) or isinstance(v, int): return ('i', int(v))
    if isinstance(v, float): return ('i', int(v)) if v == int(v) else ('f', v)
    if isinstance(v, bytes): return ('b', v)
    return None
_OK = (ast.Constant, ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Name, ast.Attribute, ast.UnaryOp, ast.BinOp, ast.Load,
       ast.USub, ast.UAdd, ast.Not, ast.Invert, ast.Add, ast.Sub, ast.Mult, ast.BitOr, ast.BitAnd, ast.LShift, ast.RShift,
       ast.Pow, ast.Div, ast.FloorDiv, ast.Mod, ast.keyword, ast.Starred)
def _dicts(tree):
    out = {}; dup = set()
    def walk(body, qual):
        for st in body:
            if isinstance(st, ast.ClassDef): walk(st.body, qual + [st.name]); continue
            if not qual or not isinstance(st, (ast.Assign, ast.AnnAssign)) or not isinstance(st.value, ast.Dict): continue
            tg = st.targets if isinstance(st, ast.Assign) else [st.target]
            if len(tg) != 1 or not isinstance(tg[0], ast.Name): continue
            n = ('.'.join(qual), tg[0].id)
            if n in out: dup.add(n)
            out[n] = st
    walk(tree.body, [])
    return out, dup
class _Blank(ast.NodeTransformer):
    def __init__(self, ids): self.ids = ids
    def visit_Assign(self, n): return ast.Assign(targets=n.targets, value=ast.Constant('__BRD__')) if id(n) in self.ids else n
    def visit_AnnAssign(self, n):
        if id(n) in self.ids: n.value = ast.Constant('__BRD__')
        return n
def _classbody(tree, qual):
    body = tree.body
    for part in qual.split('.'):
        nxt = [s for s in body if isinstance(s, ast.ClassDef) and s.name == part]
        if len(nxt) != 1: return None
        body = nxt[0].body
    return body
def _refs_attr(body, attr, skip):
    for st in body:
        if st is skip or isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): continue
        for x in ast.walk(st):
            if isinstance(x, ast.Name) and x.id == attr: return True
            if isinstance(x, (ast.Lambda,)): pass
    return False
def _safe_value(v, old_callees):
    for x in ast.walk(v):
        if isinstance(x, ast.Lambda): continue
        if isinstance(x, (ast.arguments, ast.arg)): continue
        if isinstance(x, ast.Call):
            if ast.dump(x.func) not in old_callees: return False
            continue
        if not isinstance(x, _OK) and not _inside_lambda(v, x): return False
    return True
def _inside_lambda(root, node):
    for l in ast.walk(root):
        if isinstance(l, ast.Lambda) and any(n is node for n in ast.walk(l)): return True
    return False
def narrow(files_old_new):
    """files_old_new: list of (rel, old_text, new_text) for every changed file. Returns (set_of_tests, note) or (None, reason)."""
    total = set(); notes = []
    for rel, old, new in files_old_new:
        if not (rel.startswith('sqlglot/') and rel.endswith('.py')) or old is None or new is None: return None, 'not sqlglot py edit'
        try: to, tn = ast.parse(old), ast.parse(new)
        except SyntaxError: return None, 'syntax'
        do, dupo = _dicts(to); dn, dupn = _dicts(tn)
        if set(do) != set(dn): return None, 'dict set changed'
        changed = [n for n in do if ast.dump(do[n].value) != ast.dump(dn[n].value)]
        if not changed: return None, 'no dict change'
        if any(n in dupo or n in dupn for n in changed): return None, 'dup assignment'
        if ast.dump(_Blank({id(do[n]) for n in changed}).visit(to)) != ast.dump(_Blank({id(dn[n]) for n in changed}).visit(tn)):
            return None, 'change outside dict literals'
        to, tn = ast.parse(old), ast.parse(new); do, _ = _dicts(to); dn, _ = _dicts(tn)
        mod = rel[:-3].replace('/', '.').removesuffix('.__init__')
        sh = _shard(mod)
        old_callees = {ast.dump(x.func) for x in ast.walk(to) if isinstance(x, ast.Call)}
        for qual, attr in changed:
            name = f'{mod}:{qual}.{attr}'
            info = sh.get(name)
            if info is None: return None, f'untracked {name}'
            body = _classbody(tn, qual)
            if body is None or _refs_attr(body, attr, dn[(qual, attr)]): return None, f'class body refs {attr}'
            bo = _classbody(to, qual)
            if bo is None or _refs_attr(bo, attr, do[(qual, attr)]): return None, f'class body refs {attr}'
            ov, nv = do[(qual, attr)].value, dn[(qual, attr)].value
            def entries(d):
                seq = []; keys = {}
                for k, v in zip(d.keys, d.values):
                    if k is None: seq.append(('**', ast.dump(v))); continue
                    ku = ast.unparse(k)
                    if ku in keys: return None, None
                    keys[ku] = (k, v); seq.append((ku, ast.dump(v)))
                return seq, keys
            so, ko = entries(ov); sn, kn = entries(nv)
            if so is None or sn is None: return None, 'dup key'
            ch = {k for k in set(ko) | set(kn) if k not in ko or k not in kn or ast.dump(ko[k][1]) != ast.dump(kn[k][1])}
            if [e for e in so if e[0] not in ch] != [e for e in sn if e[0] not in ch]: return None, 'order/spread change'
            for k in ch:
                if k in kn and not (_safe_value(kn[k][1], old_callees) and _safe_value(kn[k][0], old_callees)): return None, 'unsafe new expr'
                r = info['keyexpr'].get(k)
                if r is None:
                    node = (kn.get(k) or ko.get(k))[0]
                    r = kr_const(node.value) if isinstance(node, ast.Constant) else None
                if r is None or r[0] == '?': return None, f'unresolved key {k}'
                valonly = k in ko and k in kn
                imp = info['imp']
                if any(kind in 'VW' for _, kind in imp) or (r, 'v') in imp or any(kk is not None and kk[0] == '?' for kk, _ in imp):
                    return None, f'import-read {name}[{k}]'
                if not valonly and ((r, 'm') in imp or any(kind == 'K' for _, kind in imp)): return None, f'import-membership {name}[{k}]'
                if valonly and info['keys'] is not None and r not in info['keys']: return None, 'key not in recorded dict'
                aff = set(info['v'].get(r, ())) | info['V'] | info['q']
                if not valonly: aff |= set(info['m'].get(r, ())) | info['K']
                total |= aff; notes.append(f'{qual}.{attr}[{k}]{"=" if valonly else "+-"}{len(aff)}')
    return total | _unknown(), 'dictnarrow:' + ','.join(notes)

class _LStrip(ast.NodeTransformer):
    def visit_Lambda(self, n):
        n.body = ast.Constant('__BRL__'); return n
def _ldump(node):
    import copy
    return ast.dump(_LStrip().visit(copy.deepcopy(node)))
def readers(files_old_new):
    """Safety net for class-level dict-literal edits that narrow() refuses: every test that read ANY part of a changed
    tracked dict at test time (any key lookup, membership, whole-dict op) + tests whose tracked build run did not pass.
    Returns a set (possibly empty). Only dicts whose source text differs are compared (cheap text check first)."""
    out = set()
    for rel, old, new in files_old_new:
        if not (rel.startswith('sqlglot/') and rel.endswith('.py')) or old is None or new is None: continue
        mod = rel[:-3].replace('/', '.').removesuffix('.__init__')
        sh = _shard(mod)
        if not sh: continue
        try: to, tn = _parse_cached(old), ast.parse(new)
        except SyntaxError: continue
        do, _ = _dicts(to); dn, _ = _dicts(tn)
        ol, nl = old.split('\n'), new.split('\n')
        def seg(lines, st): return '\n'.join(lines[st.lineno - 1:st.end_lineno])
        names = set()
        for n in set(do) | set(dn):
            if n not in do or n not in dn: names.add(n); continue
            if seg(ol, do[n]) == seg(nl, dn[n]): continue          # identical source text -> identical literal
            if _ldump(do[n].value) != _ldump(dn[n].value): names.add(n)
        if not names: continue
        for qual, attr in names:
            info = sh.get(f'{mod}:{qual}.{attr}')
            if not info: continue
            for s in info['v'].values(): out |= s
            for s in info['m'].values(): out |= s
            out |= info['K'] | info['V'] | info['q']
        out |= _unknown()
    return out
_PC = {}
def _parse_cached(text):
    k = hash(text)
    if k not in _PC: _PC[k] = ast.parse(text)
    return _PC[k]
