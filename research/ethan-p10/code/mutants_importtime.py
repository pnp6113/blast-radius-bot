"""Local validation only: mutate constants in module/class-body (import-time) statements of sqlglot.
Usage: python mutants_importtime.py <sqlglot_dir> <out_dir> <n> <seed>"""
import sys, ast, random, re
from pathlib import Path
src, out, n, seed = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); out.mkdir(parents=True, exist_ok=True)
files = [p for p in (src/'sqlglot').rglob('*.py')]
cands = []
for p in files:
    tree = ast.parse(p.read_text()); lines = p.read_text().split('\n')
    def visit(node):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef): visit(ch)
            elif isinstance(ch, (ast.Assign, ast.AnnAssign)) and ch.lineno == ch.end_lineno or (isinstance(ch, (ast.Assign, ast.AnnAssign))):
                for l in range(ch.lineno, ch.end_lineno + 1):
                    s = lines[l-1]
                    if re.search(r'\bTrue\b|\bFalse\b|\b\d+\b|"[A-Z_]{2,}"', s) and not s.strip().startswith('#'):
                        cands.append((p, l))
    visit(tree)
rng.shuffle(cands); made = 0
for p, l in cands:
    if made >= n: break
    lines = p.read_text().split('\n'); old = lines[l-1]
    opts = []
    for m in re.finditer(r'\bTrue\b|\bFalse\b', old): opts.append((m, 'False' if m.group() == 'True' else 'True'))
    for m in re.finditer(r'\b(\d+)\b', old): opts.append((m, str(int(m.group()) + 1)))
    for m in re.finditer(r'"([A-Z_]{2,})"', old): opts.append((m, '"' + m.group(1) + 'X"'))
    if not opts: continue
    m, rep = rng.choice(opts); new = old[:m.start()] + rep + old[m.end():]
    mod = lines[:]; mod[l-1] = new
    try:
        if ast.dump(ast.parse('\n'.join(mod))) == ast.dump(ast.parse('\n'.join(lines))): continue
    except SyntaxError: continue
    rel = str(p.relative_to(src)); a = max(1, l-3); b = min(len(lines), l+3)
    hunk = [f'@@ -{a},{b-a+1} +{a},{b-a+1} @@'] + [(' ' + lines[i-1]) if i != l else ('-' + old + '\n+' + new) for i in range(a, b+1)]
    (out / f'i{seed}_{made:04d}.patch').write_text(f'diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n' + '\n'.join(hunk) + '\n'); made += 1
print('made', made, 'from', len(cands))
