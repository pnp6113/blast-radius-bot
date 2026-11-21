"""Per-run memo of AST work on source texts (same old file is parsed by several analysis steps)."""
import ast, functools
@functools.lru_cache(maxsize=256)
def parse(text): return ast.parse(text)          # callers must not mutate the returned tree
@functools.lru_cache(maxsize=256)
def dump(text): return ast.dump(ast.parse(text))
class _Strip(ast.NodeTransformer):
    def visit_FunctionDef(self, n):
        n.decorator_list = [self.visit(d) for d in n.decorator_list]; n.args = self.visit(n.args)
        n.body = [ast.Pass()]; return n
    visit_AsyncFunctionDef = visit_FunctionDef
    def visit_Lambda(self, n):
        n.args = self.visit(n.args); n.body = ast.Constant(None); return n
@functools.lru_cache(maxsize=256)
def sdump(text):
    """dump with every def/lambda body removed (what runs at import time, structurally)."""
    return ast.dump(_Strip().visit(ast.parse(text)), include_attributes=False)

import hashlib, os, pickle
_IDX = None
def _idx():
    global _IDX
    if _IDX is None:
        p = os.environ.get('BR_ASTIDX', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'astindex.pkl'))
        try: _IDX = pickle.load(open(p, 'rb'))
        except Exception: _IDX = {}
    return _IDX
def _h(s): return hashlib.sha1(s.encode()).hexdigest()
def info(old):
    """Build-time facts for an unmodified pinned-commit file text (None if unknown)."""
    return _idx().get(_h(old))
def same_dump(old, new):
    i = info(old)
    return (_h(dump(new)) == i['dump']) if i else dump(old) == dump(new)
def same_sdump(old, new):
    i = info(old)
    return (_h(sdump(new)) == i['sdump']) if i else sdump(old) == sdump(new)

# ---- fast exact equality of dump()/sdump() between an old and a new text (skips statements whose source text is
# identical; recurses into class/def/compound bodies; falls back to a full comparison on any structural mismatch).
import copy as _copy
_BODY_FIELDS = ('body', 'orelse', 'finalbody', 'handlers', 'cases')
def _seg(lines, n): return '\n'.join(lines[n.lineno - 1:n.end_lineno])
def _hdr(n, strip):
    """dump of node with its statement-list fields replaced by placeholders"""
    c = _copy.copy(n)
    for f in _BODY_FIELDS:
        if hasattr(c, f): setattr(c, f, [])
    if strip:
        c = _Strip().visit(_copy.deepcopy(c))
    return ast.dump(c, include_attributes=False)
def _eq_stmt(a, b, la, lb, strip):
    if type(a) is not type(b): return False
    if _seg(la, a) == _seg(lb, b): return True
    if strip and isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _hdr(a, True) == _hdr(b, True)    # body is replaced by `pass` in sdump
    if any(getattr(a, f, None) for f in _BODY_FIELDS) or any(getattr(b, f, None) for f in _BODY_FIELDS):
        if _hdr(a, strip) != _hdr(b, strip): return False
        for f in _BODY_FIELDS:
            xa, xb = getattr(a, f, None), getattr(b, f, None)
            if xa is None and xb is None: continue
            if xa is None or xb is None or len(xa) != len(xb): return False
            for p, q in zip(xa, xb):
                if isinstance(p, ast.stmt) and isinstance(q, ast.stmt):
                    if not _eq_stmt(p, q, la, lb, strip): return False
                elif ast.dump(_Strip().visit(_copy.deepcopy(p)) if strip else p, include_attributes=False) != \
                        ast.dump(_Strip().visit(_copy.deepcopy(q)) if strip else q, include_attributes=False):
                    return False
        return True
    if strip:
        return ast.dump(_Strip().visit(_copy.deepcopy(a)), include_attributes=False) == ast.dump(_Strip().visit(_copy.deepcopy(b)), include_attributes=False)
    return ast.dump(a) == ast.dump(b)
def fast_equal(old, new, strip):
    ta, tb = parse(old), parse(new)
    if len(ta.body) != len(tb.body) or ta.type_ignores or tb.type_ignores: return None
    la, lb = old.split('\n'), new.split('\n')
    return all(_eq_stmt(a, b, la, lb, strip) for a, b in zip(ta.body, tb.body))
_slow_same_dump, _slow_same_sdump = same_dump, same_sdump
def same_dump(old, new):
    if os.environ.get('BR_FASTEQ', '1') == '1':
        r = fast_equal(old, new, False)
        if r is not None: return r
    return _slow_same_dump(old, new)
def same_sdump(old, new):
    if os.environ.get('BR_FASTEQ', '1') == '1':
        r = fast_equal(old, new, True)
        if r is not None: return r
    return _slow_same_sdump(old, new)
