"""pytest plugin for coverage collection: run ProcessPoolExecutor work in-process (with pickle round-trips,
like spawn would), so per-test coverage contexts see it."""
import concurrent.futures as cf, pickle
class SyncExecutor(cf.Executor):
    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kw):
        if initializer: initializer(*pickle.loads(pickle.dumps(initargs)))
    def submit(self, fn, /, *args, **kwargs):
        f = cf.Future()
        try:
            a, k = pickle.loads(pickle.dumps((args, kwargs)))
            f.set_result(pickle.loads(pickle.dumps(fn(*a, **k))))
        except BaseException as e:
            f.set_exception(e)
        return f
    def shutdown(self, wait=True, *, cancel_futures=False): pass
cf.ProcessPoolExecutor = SyncExecutor
import concurrent.futures.process as cfp
cfp.ProcessPoolExecutor = SyncExecutor
