"""BUILD TIME (free): run expensive setUpClass of every test class once at the pinned commit and pickle the resulting
class state + the sqlglot code it executed + files it opened. Usage: python build_presets.py <sqlglot> <tests.json> <out.pkl>"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
src, tj, out = os.path.abspath(sys.argv[1]), sys.argv[2], os.path.abspath(sys.argv[3])
tests = json.load(open(tj))['tests']
import fastprobe as fp
mods = sorted({t['file'][:-3].replace('/', '.') for t in tests})
fp.warm(src, mods)
fp.preset(sorted({(t['file'][:-3].replace('/', '.'), t['id'].split('::')[1]) for t in tests}))
print('saved', fp.save_presets(out))
