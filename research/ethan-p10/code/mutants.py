"""Local validation only: generate synthetic single-line mutants of the pinned sqlglot (similar in spirit to
the sample's kinds), as unified diffs. NEVER ship the results as a mutant->tests table (rule: no lookup by kind
of edit). Usage: python mutants.py <sqlglot_dir> <covmap> <out_dir> <n> <seed>"""
import sys, os, re, json, random, ast
from pathlib import Path
src, covp, out, n, seed = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
rng = random.Random(seed); out.mkdir(parents=True, exist_ok=True)
cov = json.load(open(covp))['line_tests']
OPS = [
    (r' == ', ' != '), (r' != ', ' == '), (r' < ', ' <= '), (r' <= ', ' < '), (r' > ', ' >= '), (r' >= ', ' > '),
    (r' and ', ' or '), (r' or ', ' and '), (r'\bnot ', ''), (r'\bif ', 'if not '), (r' is not ', ' is '),
    (r' is ', ' == '), (r'\bTrue\b', 'False'), (r'\bFalse\b', 'True'), (r'\b(\d+)\b', None), (r' \+ ', ' - '),
    (r'\[0\]', '[1]'), (r'\[-1\]', '[0]'), (r'copy=False', 'copy=True'), (r'\bNone\b', '0'),
]
def mutate(line):
    opts = [(p, r) for p, r in OPS if re.search(p, line)]
    rng.shuffle(opts)
    for p, r in opts:
        ms = list(re.finditer(p, line)); m = rng.choice(ms)
        rep = str(int(m.group(1)) + 1) if r is None else r
        new = line[:m.start()] + rep + line[m.end():]
        if new != line: return new
    return None
files = [f for f in cov if f.startswith('sqlglot/') and f.endswith('.py')]
made = 0; tries = 0
while made < n and tries < n * 50:
    tries += 1
    f = rng.choice(files); lines = (src / f).read_text().split('\n')
    l = int(rng.choice(list(cov[f].keys())))
    old = lines[l - 1]
    if old.strip().startswith(('#', '"', "'", 'def ', 'class ', '@')): continue
    new = mutate(old)
    if not new: continue
    mod = lines[:]; mod[l - 1] = new
    try: ast.parse('\n'.join(mod))
    except SyntaxError: continue
    if ast.dump(ast.parse('\n'.join(mod))) == ast.dump(ast.parse('\n'.join(lines))): continue
    a = max(1, l - 3); b = min(len(lines), l + 3)
    hunk = [f'@@ -{a},{b-a+1} +{a},{b-a+1} @@']
    for i in range(a, b + 1):
        if i == l: hunk += ['-' + old, '+' + new]
        else: hunk.append(' ' + lines[i - 1])
    patch = f'diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n' + '\n'.join(hunk) + '\n'
    (out / f'm{seed}_{made:04d}.patch').write_text(patch); made += 1
print('made', made)
