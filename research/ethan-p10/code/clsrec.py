"""pytest plugin (build-time, pinned commit): record which sqlglot Expression classes (incl. MRO bases) each test
instantiates. Setup-phase (setUpClass) instantiations are recorded under 'class:<file>::<Class>'; anything during
collection/import under '<collect>'."""
import os, json, collections, pytest
_cur = ['<collect>']; _rec = collections.defaultdict(set); _seen_types = {}
def pytest_configure(config):
    from sqlglot.expressions.core import Expr as Expression
    orig = Expression.__init__
    def __init__(self, **k):
        _rec[_cur[0]].add(type(self))
        return orig(self, **k)
    Expression.__init__ = __init__
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    prev = _cur[0]; _cur[0] = 'class:' + item.nodeid.rsplit('::', 1)[0]; yield; _cur[0] = item.nodeid
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _cur[0] = item.nodeid; yield
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    _cur[0] = 'class:' + item.nodeid.rsplit('::', 1)[0]; yield; _cur[0] = '<between>'
def pytest_sessionfinish(session):
    wid = os.environ.get('PYTEST_XDIST_WORKER', 'main')
    out = collections.defaultdict(set)
    for key, types in _rec.items():
        for ty in types:
            for c in ty.__mro__:
                if c is object or c.__name__ == 'Expr': break
                out[c.__name__].add(key)
    json.dump({k: sorted(v) for k, v in out.items()}, open(os.path.join(os.environ['BR_PLUG_OUT'], f'cls_{wid}.json'), 'w'))
