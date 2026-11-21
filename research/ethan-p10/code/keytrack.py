"""Key-level read tracking for class-attribute dict literals in sqlglot (BUILD TIME only).
Installs a builtins.__build_class__ hook BEFORE sqlglot is imported: every exact-dict class attribute of a class whose
__module__ starts with 'sqlglot' (and whose metaclass has no custom __prepare__) is replaced, just before the metaclass
runs, by TD (a dict subclass) that records reads into READS as (dictname, keyrepr, kind):
  kind 'v' = value/lookup of one key (getitem/get/pop...), 'm' = membership of one key,
  'K' = whole-key-set op (iter/len/keys), 'V' = whole value-exposing op (items/values/copy/eq/repr/reduce/or),
  'W' = mutation. Class-body reads that happen BEFORE the metaclass runs are not seen -> the runtime refuses to narrow
  when the class body references the attribute name anywhere else (checked statically per patch)."""
import builtins, enum, types
READS = set()
DICTS = {}          # dictname -> sorted keyreprs at creation
ON = [True]
def kr(k):
    if k is None: return ('N',)
    if isinstance(k, enum.Enum) and not isinstance(k, (str, int, float, bytes)):
        return ('E', type(k).__module__ + '.' + type(k).__qualname__, k.name)
    if isinstance(k, str): return ('s', k + '')
    if isinstance(k, bytes): return ('b', bytes(k))
    if isinstance(k, (bool, int)): return ('i', int(k))
    if isinstance(k, float): return ('i', int(k)) if k == int(k) else ('f', float(k))
    if isinstance(k, tuple): return ('T',) + tuple(kr(x) for x in k)
    if isinstance(k, type) and type(k).__eq__ is type.__eq__ and type(k).__hash__ is type.__hash__: return ('C', k.__module__, k.__qualname__)
    if isinstance(k, frozenset): return ('?', 'frozenset')
    return ('?', type(k).__qualname__)
def _r(s, k, kind):
    if ON[0]:
        try: READS.add((s._n, kr(k), kind))
        except Exception: READS.add((s._n, ('?', 'err'), kind))
def _w(s, kind):
    if ON[0]: READS.add((s._n, None, kind))
_D = dict
class TD(dict):
    __slots__ = ('_n',)
    def __getitem__(s, k): _r(s, k, 'v'); return _D.__getitem__(s, k)
    def get(s, k, *a): _r(s, k, 'v'); return _D.get(s, k, *a)
    def __contains__(s, k): _r(s, k, 'm'); return _D.__contains__(s, k)
    def __iter__(s): _w(s, 'K'); return _D.__iter__(s)
    def __reversed__(s): _w(s, 'K'); return _D.__reversed__(s)
    def __len__(s): _w(s, 'K'); return _D.__len__(s)
    def keys(s): _w(s, 'K'); return _D.keys(s)
    def items(s): _w(s, 'V'); return _D.items(s)
    def values(s): _w(s, 'V'); return _D.values(s)
    def copy(s): _w(s, 'V'); return _D.copy(s)
    def __eq__(s, o): _w(s, 'V'); return _D.__eq__(s, o)
    def __ne__(s, o): _w(s, 'V'); return _D.__ne__(s, o)
    __hash__ = None
    def __repr__(s): _w(s, 'V'); return _D.__repr__(s)
    def __reduce_ex__(s, p): _w(s, 'V'); return (_D, (_D(_D.items(s)),))
    def __reduce__(s): _w(s, 'V'); return (_D, (_D(_D.items(s)),))
    def __or__(s, o): _w(s, 'V'); return _D.__or__(_D(_D.items(s)), o)
    def __ror__(s, o): _w(s, 'V'); return _D.__or__(_D(o), _D(_D.items(s)))
    def __ior__(s, o): _w(s, 'W'); _D.update(s, o); return s
    def __setitem__(s, k, v): _w(s, 'W'); _D.__setitem__(s, k, v)
    def __delitem__(s, k): _w(s, 'W'); _D.__delitem__(s, k)
    def setdefault(s, *a): _w(s, 'W'); return _D.setdefault(s, *a)
    def pop(s, *a): _w(s, 'W'); return _D.pop(s, *a)
    def popitem(s): _w(s, 'W'); return _D.popitem(s)
    def update(s, *a, **k): _w(s, 'W'); return _D.update(s, *a, **k)
    def clear(s): _w(s, 'W'); return _D.clear(s)
    def __sizeof__(s): return _D.__sizeof__(s)
    def __format__(s, spec): _w(s, 'V'); return _D.__format__(s, spec)
_orig = builtins.__build_class__
def _bc(func, name, *bases, **kw):
    g = getattr(func, '__globals__', {}).get('__name__', '')
    if not (isinstance(g, str) and g.startswith('sqlglot')): return _orig(func, name, *bases, **kw)
    try:
        meta, _, kwds = types.prepare_class(name, types.resolve_bases(bases), dict(kw))
    except Exception:
        return _orig(func, name, *bases, **kw)
    if not isinstance(meta, type) or any('__prepare__' in c.__dict__ for c in meta.__mro__ if c is not type):
        return _orig(func, name, *bases, **kw)
    def wrap(n, b, ns, **k):
        mod = ns.get('__module__', '')
        if isinstance(mod, str) and mod.startswith('sqlglot') and type(ns) is dict:
            for a, v in list(ns.items()):
                if type(v) is dict and not a.startswith('__'):
                    t = TD(v); t._n = f"{mod}:{ns.get('__qualname__', n)}.{a}"
                    try: DICTS[t._n] = sorted(kr(x) for x in _D.keys(v))
                    except TypeError: DICTS[t._n] = None
                    ns[a] = t
        return meta(n, b, ns, **k)
    kw2 = {k: v for k, v in kw.items() if k != 'metaclass'}
    return _orig(func, name, *bases, metaclass=wrap, **kw2)
def install():
    builtins.__build_class__ = _bc
