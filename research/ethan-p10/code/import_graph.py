"""Static import graph of sqlglot + tests at the pinned commit (AST-based, includes function-level imports)."""
import ast, json, sys, os
from pathlib import Path
from collections import defaultdict
ROOT = Path(sys.argv[1])
def modname(p):
    rel = p.relative_to(ROOT).with_suffix('')
    parts = list(rel.parts)
    if parts[-1] == '__init__': parts = parts[:-1]
    return '.'.join(parts)
files = [p for p in list((ROOT/'sqlglot').rglob('*.py')) + list((ROOT/'tests').rglob('*.py'))]
mods = {modname(p): p for p in files}
edges = defaultdict(set); lazy = defaultdict(set)
for m, p in mods.items():
    tree = ast.parse(p.read_text())
    pkg = m if p.name == '__init__.py' else m.rsplit('.', 1)[0]
    toplevel = set(id(n) for n in tree.body)
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                b = pkg.split('.')
                b = b[:len(b) - (node.level - 1)]
                base = '.'.join(b + ([base] if base else []))
            targets = [base] + [f"{base}.{a.name}" for a in node.names]
        for t in targets:
            # resolve to longest known module prefix
            while t and t not in mods: t = t.rsplit('.', 1)[0] if '.' in t else ''
            if t and t != m:
                (edges if id(node) in toplevel else lazy)[m].add(t)
# Note: packages imply their __init__ executes; importing a.b.c imports a and a.b too
def parents(t):
    out = []; parts = t.split('.')
    for i in range(1, len(parts)): 
        q = '.'.join(parts[:i])
        if q in mods: out.append(q)
    return out
def closure(m, use_lazy):
    seen = set(); st = [m]
    while st:
        x = st.pop()
        if x in seen: continue
        seen.add(x)
        nxt = set(edges[x]) | (lazy[x] if use_lazy else set())
        for y in list(nxt): nxt |= set(parents(y))
        st.extend(nxt)
    return seen
out = {'edges': {k: sorted(v) for k, v in edges.items()}, 'lazy_edges': {k: sorted(v) for k, v in lazy.items()}}
tests = sorted(m for m in mods if m.startswith('tests.') and mods[m].name.startswith('test_'))
out['test_closure_static'] = {t: sorted(x for x in closure(t, False) if x.startswith('sqlglot')) for t in tests}
out['test_closure_with_lazy'] = {t: sorted(x for x in closure(t, True) if x.startswith('sqlglot')) for t in tests}
fanin = defaultdict(int)
for t, c in out['test_closure_static'].items():
    for x in c: fanin[x] += 1
out['src_fanin_static'] = dict(sorted(fanin.items(), key=lambda kv: -kv[1]))
json.dump(out, open(sys.argv[2], 'w'), indent=1)
print('modules', len(mods), 'test modules', len(tests))
import statistics
print('static closure sizes:', statistics.median(len(v) for v in out['test_closure_static'].values()), 'median of', len([m for m in mods if m.startswith('sqlglot')]), 'src modules')
print('with lazy:', statistics.median(len(v) for v in out['test_closure_with_lazy'].values()))
