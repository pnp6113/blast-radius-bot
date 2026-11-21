"""Local validation only: single-line mutants on lines strictly inside multi-line lambda bodies (any parent).
Usage: python mutants_lambda.py <sqlglot_dir> <out_dir> <n> <seed>"""
import sys, re, random, ast
from pathlib import Path
src, out, n, seed = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); out.mkdir(parents=True, exist_ok=True)
OPS = [(r' == ', ' != '), (r' != ', ' == '), (r' and ', ' or '), (r' or ', ' and '), (r'\bnot ', ''), (r'\bTrue\b', 'False'),
       (r'\bFalse\b', 'True'), (r'\b(\d+)\b', None), (r'\[0\]', '[1]'), (r'\bNone\b', '0'), (r'"([A-Z_]+)"', 'X'), (r'exp\.(\w+)', 'Y')]
def mutate(line):
    opts = [(p, r) for p, r in OPS if re.search(p, line)]; rng.shuffle(opts)
    for p, r in opts:
        m = rng.choice(list(re.finditer(p, line)))
        if r is None: rep = str(int(m.group(1)) + 1)
        elif r == 'X': rep = '"' + m.group(1) + '_X"'
        elif r == 'Y': rep = 'exp.' + ('Anonymous' if m.group(1) != 'Anonymous' else 'Column')
        else: rep = r
        new = line[:m.start()] + rep + line[m.end():]
        if new != line: return new
cands = []
for f in sorted((src / 'sqlglot').rglob('*.py')):
    rel = str(f.relative_to(src)); text = f.read_text(); t = ast.parse(text)
    for L in ast.walk(t):
        if isinstance(L, ast.Lambda) and L.end_lineno > L.lineno:
            for l in range(L.lineno + 1, L.end_lineno + 1): cands.append((rel, l))
rng.shuffle(cands); made = 0
for rel, l in cands:
    if made >= n: break
    lines = (src / rel).read_text().split('\n'); old = lines[l - 1]; new = mutate(old)
    if not new: continue
    mod = lines[:]; mod[l - 1] = new
    try:
        if ast.dump(ast.parse('\n'.join(mod))) == ast.dump(ast.parse('\n'.join(lines))): continue
    except SyntaxError: continue
    a = max(1, l - 3); b = min(len(lines), l + 3)
    hunk = [f'@@ -{a},{b-a+1} +{a},{b-a+1} @@'] + [('-' + old + '\n+' + new) if i == l else ' ' + lines[i - 1] for i in range(a, b + 1)]
    (out / f'L{seed}_{made:04d}.patch').write_text(f'diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n' + '\n'.join(hunk) + '\n'); made += 1
print('made', made, 'of', len(cands), 'lambda lines')
