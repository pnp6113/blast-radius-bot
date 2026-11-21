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
