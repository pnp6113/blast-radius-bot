"""pytest plugin: record which Dialect subclasses each test instantiates (per test node id)."""
import os, json, collections, pytest
_cur = ['<collect>']; _rec = collections.defaultdict(set)
def pytest_configure(config):
    from sqlglot.dialects.dialect import Dialect
    orig = Dialect.__init__
    def __init__(self, *a, **k):
        for c in type(self).__mro__:
            if c is Dialect: break
            _rec[c.__module__ + '.' + c.__name__].add(_cur[0])
        return orig(self, *a, **k)
    Dialect.__init__ = __init__
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _cur[0] = item.nodeid; yield; _cur[0] = '<between>'
def pytest_sessionfinish(session):
    wid = os.environ.get('PYTEST_XDIST_WORKER', 'main')
    json.dump({k: sorted(v) for k, v in _rec.items()}, open(os.path.join(os.environ.get('BR_PLUG_OUT', '.'), f'dialects_{wid}.json'), 'w'))
