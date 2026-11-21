"""Evaluate simple baselines on the 30 sample commits using measured maps. Real numbers only.
Usage: python eval_baselines.py <kit_dir> <sqlglot_dir> <artifacts_dir>"""
import json, sys, re, ast, math, collections, os
import astcache as AC
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
kit, src, art = map(Path, sys.argv[1:4])
_tests_file = Path(os.environ['BR_TESTS']) if os.environ.get('BR_TESTS') else kit / 'sample' / 'tests.json'
tests = json.load(open(_tests_file))['tests']
_ans_file = kit / 'sample' / 'answers.json'
ans = json.load(open(_ans_file))['commits'] if _ans_file.is_file() else {}
_cov_file = art / 'covmap_line.json'
if _cov_file.is_file():
    cov = json.load(open(_cov_file))['line_tests']
else:
    import gzip
    cov = json.load(gzip.open(art / 'covmap_line.json.gz', 'rt'))['line_tests']
reads = json.load(open(art / 'file_reads_map.json'))
ms = lambda s: math.floor(s*1000+.5)
DUR = {t['number']: ms(t['seconds']) for t in tests}
ALL = [t['number'] for t in tests]
by_class = collections.defaultdict(list)
for t in tests: by_class[t['id'].rsplit('::',1)[0]].append(t['number'])
cls_of = {t['number']: t['id'].rsplit('::',1)[0] for t in tests}

def schedule(sel):
    sel = sorted(set(sel), key=lambda n: -DUR[n])
    if not sel: return 0, []
    best = None
    for k in range(1, min(8, len(sel))+1):
        loads=[0]*k; ws=[[] for _ in range(k)]
        for n in sel:
            i=loads.index(min(loads)); loads[i]+=DUR[n]; ws[i].append(n)
        c=max(loads)+2000*k
        if best is None or c<best[0]: best=(c,ws)
    return best

def parse_patch(text):
    files=[]; cur=None
    for line in text.splitlines():
        if line.startswith('diff --git'):
            cur={'old':None,'new':None,'hunks':[]}; files.append(cur)
        elif line.startswith('--- '): cur['old']=line[4:].removeprefix('a/')
        elif line.startswith('+++ '): cur['new']=line[4:].removeprefix('b/')
        elif line.startswith('@@'):
            m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',line)
            h={'old_start':int(m[1]),'removed':[],'added':[],'added_new':[],'lines':[]}; cur['hunks'].append(h); o=int(m[1])
            h['_o']=o; h['_n']=int(m[3]); h['_after_rm']=False
        elif cur and cur['hunks']:
            h=cur['hunks'][-1]
            if line.startswith('-'): h['removed'].append(h['_o']); h['lines'].append(('-',line[1:])); h['_o']+=1; h['_after_rm']=True
            elif line.startswith('+'):
                if not (REPL and h['_after_rm']): h['added'].append(h['_o'])  # pure insertion before old line _o
                h['added_new'].append(h['_n']); h['_n']+=1; h['lines'].append(('+',line[1:]))
            elif line.startswith(' ') or line=='' : h['_o']+=1; h['_n']+=1; h['lines'].append((' ',line[1:])); h['_after_rm']=False
    return files

def apply(old_text, f):
    lines=old_text.split('\n'); out=[]; i=1
    for h in f['hunks']:
        while i<h['old_start']: out.append(lines[i-1]); i+=1
        for kind,l in h['lines']:
            if kind==' ': out.append(lines[i-1]); i+=1
            elif kind=='-': i+=1
            else: out.append(l)
    out.extend(lines[i-1:]); return '\n'.join(out)

def ast_equal(path, f):
    old=(src/path).read_text(); new=apply(old,f)
    try: return AC.same_dump(old,new)
    except SyntaxError: return False

_stmt_cache={}
def stmt_spans(path):
    if path not in _stmt_cache:
        tree=AC.parse((src/path).read_text()); spans=[]
        for n in ast.walk(tree):
            if isinstance(n, ast.stmt):
                end=n.end_lineno
                body=getattr(n,'body',None)
                if isinstance(body,list) and body and isinstance(body[0],ast.stmt):
                    end=body[0].lineno-1  # header only for compound statements
                    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.decorator_list:
                        pass
                spans.append((n.lineno,max(end,n.lineno),n))
        _stmt_cache[path]=spans
    return _stmt_cache[path]
_LEXCL={}
def touched_lines(f, path=None):
    s=set()
    for h in f['hunks']:
        s.update(h['removed'])
        for a in h['added']: s.update({a-1,a})
    s-=_LEXCL.get(id(f),set())
    if path:
        out=set(s)
        for l in s:
            best=None
            for a,b,n in stmt_spans(path):
                if a<=l<=b and (best is None or b-a < best[1]-best[0]): best=(a,b)
            if best: out.update(range(best[0],best[1]+1))
        return out
    return s

REPL = os.environ.get('BR_REPL','0')=='1'      # replacement '+' lines don't widen to old neighbours
NEWSIDE = os.environ.get('BR_NEWSIDE','0')=='1'
VO = os.environ.get('BR_VO','0')=='1'  # dict-literal values-only edits skip key/truth-only consumers # also analyse import-time statements on the patched (new) side
def import_stmt(path,line,tree=None):
    tree=tree or AC.parse((src/path).read_text())
    import_stmt.cls=None
    def visit(node, runtime):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch,(ast.FunctionDef,ast.AsyncFunctionDef,ast.Lambda)):
                if ch.lineno<=line<=ch.end_lineno and not runtime:
                    # inside a def: runtime unless it's on decorator/default lines
                    return None
                continue
            if isinstance(ch,ast.ClassDef):
                if ch.lineno<=line<=ch.end_lineno:
                    import_stmt.cls=ch.name
                    return visit(ch, runtime)
                continue
            if isinstance(ch,ast.stmt) and ch.lineno<=line<=ch.end_lineno: return ch
        return None
    return visit(tree, False)

def load_index(importtime):
    """Build-time cache of the AST name index (pinned commit only); BR_IDXCACHE=path enables it."""
    import pickle
    pc=os.environ.get('BR_IDXCACHE')
    if pc and os.path.exists(pc):
        idx=pickle.load(open(pc,'rb')); idx.cov=cov; return idx
    idx=importtime.Index(src, cov)
    if pc:
        c=idx.cov; idx.cov=None; pickle.dump(idx,open(pc,'wb'),protocol=5); idx.cov=c
    return idx

LAMBDA = os.environ.get('BR_LAMBDA','1')=='1'
class _StripL(ast.NodeTransformer):
    def visit_FunctionDef(self, n):
        n.decorator_list=[self.visit(d) for d in n.decorator_list]; n.args=self.visit(n.args); n.body=[ast.Pass()]; return n
    visit_AsyncFunctionDef=visit_FunctionDef
    def visit_Lambda(self, n):
        n.args=self.visit(n.args); n.body=ast.Constant(None); return n
def lambda_only(p, f):
    """Touched old lines that lie strictly inside the body of a lambda stored as a dict value / container element /
    plain assignment (so it is not called while the module is imported), when the patch changes nothing outside
    function and lambda bodies. Returns (excluded_lines, extra_tests) or None."""
    old=(src/p).read_text()
    new=apply(old,f)
    try: AC.parse(new)
    except SyntaxError: return None
    if not AC.same_sdump(old,new): return None
    lines=old.split('\n')
    inf=AC.info(old)
    if inf is not None: lams=inf['lams']
    else:
        ot=AC.parse(old); parent={}
        for n in ast.walk(ot):
            for c in ast.iter_child_nodes(n): parent[c]=n
        lams=[]
        for L in ast.walk(ot):
            if not (isinstance(L,ast.Lambda) and L.end_lineno>L.lineno): continue
            par=parent.get(L)
            ok=isinstance(par,(ast.Dict,ast.List,ast.Tuple,ast.Set)) or (isinstance(par,(ast.Assign,ast.AnnAssign)) and par.value is L)
            if isinstance(par,ast.Dict) and L not in par.values: ok=False
            st=L
            while st in parent and not isinstance(st,ast.stmt): st=parent[st]
            lams.append((L.lineno,L.end_lineno,L.end_col_offset,ok,(st.lineno,st.end_lineno),[(n.lineno,n.end_lineno) for n in ast.walk(st) if isinstance(n,(ast.Lambda,ast.FunctionDef,ast.AsyncFunctionDef))]))
    s=set()
    for h in f['hunks']:
        s.update(h['removed'])
        for a in h['added']: s.update({a-1,a})
    excl=set(); extra=set(); fc=cov.get(p,{})
    for l in s:
        best=None
        for L in lams:
            if L[0]<l<=L[1] and (best is None or L[1]-L[0]<best[1]-best[0]): best=L
        if best is None: continue
        lno,eno,ecol,ok,(sa,sb),fnspans=best
        tail=lines[eno-1].encode()[ecol:].decode(errors='replace').strip()
        if l==eno and tail not in ('',',',')','),','},','],','}',']'): ok=False
        if not ok: continue
        own=set(fc.get(str(l),[]))
        if not own: continue          # touched line itself not recorded (coverage folds some multi-line exprs) -> normal path
        body=set()
        for x in range(lno+1,eno+1): body.update(fc.get(str(x),[]))
        imp=set()
        for x in range(sa,sb+1):
            if not any(a<=x<=b for a,b in fnspans): imp.update(fc.get(str(x),[]))
        if own<=imp: continue      # possibly run at import time -> normal path
        excl.add(l); extra|=body
    return (excl,extra) if excl else None

def select(patch, mode):
    _LEXCL.clear()
    sel=set(); notes=[]
    for f in parse_patch(patch):
        p=f['new'] if f['new']!='/dev/null' else f['old']
        if p.endswith('.py') and p.startswith('sqlglot/'):
            if mode!='nocomment' and ast_equal(p,f): notes.append('ast-equal'); continue
            if LAMBDA and mode=='stmt+import' and f['old']!='/dev/null' and f['new']!='/dev/null':
                lo=lambda_only(p,f)
                if lo:
                    _LEXCL[id(f)]=lo[0]; sel.update(lo[1]); notes.append(('lambda-body',len(lo[0]),len(lo[1])))
                    if not touched_lines(f): continue
            if mode=='file':
                for l,ts in cov.get(p,{}).items(): sel.update(ts)
            else:
                for l in touched_lines(f, p if mode in ('stmt','stmt+import') else None): sel.update(cov.get(p,{}).get(str(l),[]))
                if mode=='stmt+import':
                    for fn in ast.walk(AC.parse((src/p).read_text())):
                        if isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)) and fn.body:
                            hdr_start=min([fn.lineno]+[d.lineno for d in fn.decorator_list]); hdr_end=fn.body[0].lineno-1
                            if any(hdr_start<=l<=hdr_end for l in touched_lines(f)):
                                for l in range(fn.body[0].lineno, fn.end_lineno+1): sel.update(cov.get(p,{}).get(str(l),[]))
                                notes.append(('signature',fn.name))
                    import importtime
                    global _IDX
                    if '_IDX' not in globals(): _IDX=load_index(importtime)
                    try: ntree=AC.parse(apply((src/p).read_text(),f))
                    except SyntaxError: sel.update(ALL); notes.append('syntax-error'); continue
                    otree=AC.parse((src/p).read_text())
                    def vo(n, cls, other):
                        if not VO: return False
                        names=importtime.assigned_names(n); hits=[]
                        def walk(node, c):
                            for ch in ast.iter_child_nodes(node):
                                if isinstance(ch, ast.ClassDef): walk(ch, ch.name)
                                elif isinstance(ch,(ast.Assign,ast.AnnAssign)) and c==cls and importtime.assigned_names(ch)==names: hits.append(ch)
                        walk(other, None)
                        return len(hits)==1 and importtime.values_only_change(n, hits[0])
                    for l in touched_lines(f):
                        n=import_stmt(p,l,otree)
                        if n is not None:
                            c=import_stmt.cls; v=vo(n,c,ntree)
                            ts,info=importtime.analyze(_IDX,p,n,enclosing_class=c,values_only=v); sel.update(ts); notes.append(('import-time',info['names'][:4],len(ts),info.get('class_filter'),'vo' if v else ''))
                    if NEWSIDE:
                        for h in f['hunks']:
                            for l in h['added_new']:
                                n=import_stmt(p,l,ntree)
                                if n is not None:
                                    c=import_stmt.cls; v=vo(n,c,otree)
                                    ts,info=importtime.analyze(_IDX,p,n,enclosing_class=c,values_only=v); sel.update(ts); notes.append(('import-time-new',info['names'][:4],len(ts),'vo' if v else ''))
        elif p.startswith('tests/fixtures/'):
            sel.update(x for x in reads.get(p,[]) if isinstance(x,int))
        else:
            sel.update(ALL); notes.append('fallback:'+p)
    return sel, notes

def run(mode, expand_class=False):
    total=0; misses=0; rows=[]
    for cid in sorted(ans):
        patch=(kit/f'sample/commits/{cid}.patch').read_text()
        sel,notes=select(patch,mode) if mode!='full' else (set(ALL),[])
        if expand_class: sel={m for n in sel for m in by_class[cls_of[n]]} if False else sel
        c,_=schedule(sel); fail=set(ans[cid]['failing']); miss=not fail<=sel
        total+=c+(300000 if miss else 0); misses+=miss
        rows.append((cid,ans[cid]['kind'],len(fail),len(sel),c/1000,'MISS' if miss else '',sorted(fail-sel)[:5],notes))
    return total/1000,misses,rows
if __name__=='__main__':
    for mode in ['full','file','line','stmt','stmt+import']:
        tot,mi,rows=run(mode)
        print(f'### {mode}: simulated={tot:.3f}s misses={mi}')
        if mode in ('stmt+import',):
            for r in rows: print('  ',r)
