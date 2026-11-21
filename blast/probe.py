"""Runtime probe (port of Ethan's prototype, team-internal): run candidate tests against the patched sqlglot in a
forked child. Patched modules are served from memory (meta-path finder), patched data files via an open() redirect,
ProcessPoolExecutor work runs in-process with pickle round-trips. Nothing is written to disk.

Production guards added: per-group timeouts scaled to recorded test time, a hard parent-side kill when the child
does not answer in time (a child stuck in C code may ignore SIGALRM), and conservative results: crash, timeout,
or any unreadable answer => every probed test counts as failing.
Linux only (fork); callers must not probe elsewhere.
"""

from __future__ import annotations

import builtins
import importlib.abc
import importlib.util
import io
import json
import os
import pickle
import select
import signal
import sys
import time
import unittest

_WARM = False


def available() -> bool:
    return hasattr(os, "fork") and sys.platform.startswith("linux")


def warm() -> None:
    """Heavy third-party imports the tests need, done once in the parent (not sqlglot itself)."""
    global _WARM
    if _WARM:
        return
    os.environ.setdefault("SKIP_INTEGRATION", "0")
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    import concurrent.futures  # noqa: F401
    import decimal  # noqa: F401
    import multiprocessing  # noqa: F401
    import sqlite3  # noqa: F401
    import tracemalloc  # noqa: F401

    try:
        import duckdb  # noqa: F401
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        import pandas.testing  # noqa: F401
    except Exception:
        pass
    _WARM = True


class _Overlay(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, mods):
        self.mods = mods  # modname -> (path, source)

    def find_spec(self, name, path, target=None):
        if name in self.mods:
            p, _ = self.mods[name]
            sub = [os.path.dirname(p)] if p.endswith("__init__.py") else None
            return importlib.util.spec_from_file_location(name, p, loader=self, submodule_search_locations=sub)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        p, s = self.mods[module.__name__]
        exec(compile(s, p, "exec"), module.__dict__)


def _sync_pool():
    import concurrent.futures as cf
    import concurrent.futures.process as cfp

    class SyncExecutor(cf.Executor):
        def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kw):
            if initializer:
                initializer(*pickle.loads(pickle.dumps(initargs)))

        def submit(self, fn, /, *a, **k):
            f = cf.Future()
            try:
                a, k = pickle.loads(pickle.dumps((a, k)))
                f.set_result(pickle.loads(pickle.dumps(fn(*a, **k))))
            except BaseException as e:  # noqa: BLE001
                f.set_exception(e)
            return f

        def shutdown(self, wait=True, *, cancel_futures=False):
            pass

    cf.ProcessPoolExecutor = cfp.ProcessPoolExecutor = SyncExecutor


class _Rec(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.bad = set()

    def addFailure(self, t, e):
        self.bad.add(t.id())

    def addError(self, t, e):
        self.bad.add(getattr(t, "id", lambda: str(t))())

    def addSubTest(self, t, st, e):
        if e is not None:
            self.bad.add(t.id())

    def addUnexpectedSuccess(self, t):
        self.bad.add(t.id())


def _child(src, files, groups, wfd):
    os.chdir(src)
    sys.path.insert(0, src)
    py, data = {}, {}
    for rel, text in files.items():
        ap = os.path.join(src, rel)
        if rel.endswith(".py"):
            mod = rel[:-3].replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            py[mod] = (ap, text)
        else:
            data[ap] = text
    sys.meta_path.insert(0, _Overlay(py))
    if data:
        real_open = builtins.open

        def fake_open(file, mode="r", *a, **k):
            if isinstance(file, (str, os.PathLike)) and "r" in mode:
                ap = os.path.abspath(os.fspath(file))
                if ap in data:
                    t = data[ap]
                    return io.BytesIO(t.encode()) if "b" in mode else io.StringIO(t)
            return real_open(file, mode, *a, **k)

        builtins.open = fake_open
        io.open = fake_open
    _sync_pool()
    res = {}
    loader = unittest.TestLoader()

    def alarm(*_):
        raise TimeoutError("probe timeout")

    signal.signal(signal.SIGALRM, alarm)
    for (f, cls), meths, timeout_s in groups:
        modname = f[:-3].replace("/", ".")
        ids = [f"{f}::{cls}::{m}" for m in meths]
        r = _Rec()
        signal.setitimer(signal.ITIMER_REAL, timeout_s)
        try:
            mod = importlib.import_module(modname)
            suite = unittest.TestSuite([loader.loadTestsFromName(f"{cls}.{m}", mod) for m in meths])
            suite.run(r)  # one suite per class => setUpClass runs once
            bad_class = any("setUpClass" in str(t) or "tearDownClass" in str(t) or "setUpModule" in str(t)
                            for t, _ in r.errors)
            for m, i in zip(meths, ids):
                tid_dot = f"{modname}.{cls}.{m}"
                failed = bad_class or tid_dot in r.bad or any(
                    tid_dot == getattr(t, "id", lambda: "")() for t, _ in r.errors + r.failures)
                res[i] = "fail" if failed else "pass"
        except BaseException:  # noqa: BLE001  import error, timeout, anything => fail
            for i in ids:
                res[i] = "fail"
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    os.write(wfd, json.dumps(res).encode())
    os.close(wfd)
    os._exit(0)


def probe(src: str, files: dict, tests: list[tuple[str, float]], deadline: float, slack: float = 3.0,
          factor: float = 3.0) -> dict:
    """tests: [(test_id, recorded_seconds)]. Returns {test_id: 'pass'|'fail'}; anything uncertain is 'fail'."""
    if not tests:
        return {}
    groups_d: dict = {}
    for tid, sec in tests:
        f, cls, meth = tid.split("::")
        groups_d.setdefault((f, cls), []).append((meth, sec))
    groups = [((f, c), [m for m, _ in ms], factor * sum(s for _, s in ms) + slack) for (f, c), ms in groups_d.items()]
    budget = sum(g[2] for g in groups) + 2.0
    hard = min(time.monotonic() + budget, deadline)
    if hard - time.monotonic() < 1.0:
        return {tid: "fail" for tid, _ in tests}
    src = os.path.abspath(src)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        try:
            _child(src, files, groups, w)
        finally:
            os._exit(3)
    os.close(w)
    buf = b""
    killed = False
    while True:
        left = hard - time.monotonic()
        if left <= 0:
            os.kill(pid, signal.SIGKILL)
            killed = True
            break
        ready, _, _ = select.select([r], [], [], left)
        if not ready:
            continue
        chunk = os.read(r, 1 << 16)
        if not chunk:
            break
        buf += chunk
    os.close(r)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    if killed:
        return {tid: "fail" for tid, _ in tests}
    try:
        out = json.loads(buf)
    except Exception:  # noqa: BLE001
        return {tid: "fail" for tid, _ in tests}
    return {tid: (out.get(tid) if out.get(tid) == "pass" else "fail") for tid, _ in tests}
