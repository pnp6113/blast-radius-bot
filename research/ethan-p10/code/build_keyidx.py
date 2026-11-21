"""BUILD TIME: merge one or more keymap pickles (different PYTHONHASHSEEDs) into per-module shards
opt3/keyidx/<module>.pkl = {dictname: {'keys': keyreprs, 'v': {kr: frozenset(tests)}, 'm': {...}, 'K': fs, 'V': fs,
 'q': fs (tests that looked up an unclassifiable key), 'imp': set((kr|None, kind))}} + 'unknown' tests in _meta.pkl.
Usage: python build_keyidx.py <outdir> keymap1.pkl [keymap2.pkl ...]"""
import sys, os, pickle, collections
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
dicts = {}; imp = collections.defaultdict(set); unknown = set(); keyexpr = {}
v = collections.defaultdict(lambda: collections.defaultdict(set)); m = collections.defaultdict(lambda: collections.defaultdict(set))
K = collections.defaultdict(set); V = collections.defaultdict(set); Q = collections.defaultdict(set)
for f in sys.argv[2:]:
    d = pickle.load(open(f, 'rb'))
    for k, ks in d['dicts'].items():
        if k in dicts and dicts[k] != ks: dicts[k] = None
        else: dicts.setdefault(k, ks)
    for n, k, kind in d['import']: imp[n].add((k, kind))
    unknown |= d['unknown']; keyexpr.update(d['keyexpr'])
    for t, reads in d['tests'].items():
        for n, k, kind in reads:
            if kind in 'VW': V[n].add(t)
            elif kind == 'K': K[n].add(t)
            elif k is not None and k[0] == '?': Q[n].add(t)
            elif kind == 'v': v[n][k].add(t)
            else: m[n][k].add(t)
shards = collections.defaultdict(dict)
for n in dicts:
    mod = n.split(':')[0]
    shards[mod][n] = {'keys': dicts[n], 'v': {k: frozenset(s) for k, s in v[n].items()}, 'm': {k: frozenset(s) for k, s in m[n].items()},
                      'K': frozenset(K[n]), 'V': frozenset(V[n]), 'q': frozenset(Q[n]), 'imp': imp[n],
                      'keyexpr': {e: r for (nn, e), r in keyexpr.items() if nn == n}}
for mod, sh in shards.items(): pickle.dump(sh, open(os.path.join(out, mod + '.pkl'), 'wb'), protocol=5)
pickle.dump({'unknown': unknown}, open(os.path.join(out, '_meta.pkl'), 'wb'))
print('shards', len(shards), 'dicts', len(dicts), 'unknown', len(unknown))
