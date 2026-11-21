import sys, json, os, collections
_cur = ['<collect>']
_rec = collections.defaultdict(set)
ROOT = os.getcwd()
def _hook(event, args):
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        p = os.fsdecode(args[0])
        ap = os.path.abspath(p)
        if ap.startswith(ROOT) and not ap.endswith(('.py', '.pyc')) and '__pycache__' not in ap:
            _rec[os.path.relpath(ap, ROOT)].add(_cur[0])
sys.addaudithook(_hook)
import pytest
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _cur[0] = item.nodeid
    yield
    _cur[0] = '<between>'
def pytest_sessionfinish(session):
    wid = os.environ.get('PYTEST_XDIST_WORKER', 'main')
    json.dump({k: sorted(v) for k, v in _rec.items()}, open(os.path.join(os.environ.get('BR_PLUG_OUT', '.'), f'opens_{wid}.json'), 'w'))
