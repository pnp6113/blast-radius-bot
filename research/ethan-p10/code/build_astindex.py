"""BUILD TIME (free): per sqlglot source file at the pinned commit, keyed by sha1 of the file text:
  dump/sdump hashes (AST and AST-with-def/lambda-bodies-stripped) and the multi-line lambda index used by the
  lambda-body rule. Usage: python build_astindex.py <sqlglot_dir> <out.pkl>"""
import sys, os, ast, hashlib, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astcache as AC
src, out = sys.argv[1], sys.argv[2]
idx = {}
for root, _, fs in os.walk(os.path.join(src, 'sqlglot')):
    for f in fs:
        if not f.endswith('.py'): continue
        text = open(os.path.join(root, f)).read(); key = hashlib.sha1(text.encode()).hexdigest()
        t = ast.parse(text); parent = {}
        for n in ast.walk(t):
            for c in ast.iter_child_nodes(n): parent[c] = n
        lams = []
        for L in ast.walk(t):
            if not (isinstance(L, ast.Lambda) and L.end_lineno > L.lineno): continue
            par = parent.get(L)
            ok = isinstance(par, (ast.Dict, ast.List, ast.Tuple, ast.Set)) or (isinstance(par, (ast.Assign, ast.AnnAssign)) and par.value is L)
            if isinstance(par, ast.Dict) and L not in par.values: ok = False
            st = L
            while st in parent and not isinstance(st, ast.stmt): st = parent[st]
            fns = [(n.lineno, n.end_lineno) for n in ast.walk(st) if isinstance(n, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef))]
            lams.append((L.lineno, L.end_lineno, L.end_col_offset, ok, (st.lineno, st.end_lineno), fns))
        h = lambda s: hashlib.sha1(s.encode()).hexdigest()
        class _B(ast.NodeTransformer):
            def visit_Constant(self, node):
                node.value = None
                return node
        consts = [n.value for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Constant)]
        idx[key] = {'dump': h(AC.dump(text)), 'sdump': h(AC.sdump(text)), 'lams': lams,
                    'cdump': h(ast.dump(_B().visit(ast.parse(text)))), 'consts': consts}
pickle.dump(idx, open(out, 'wb'), protocol=5)
print('files', len(idx))
