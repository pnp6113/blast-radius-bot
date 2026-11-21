import sys, os, importlib, pathlib
os.environ.setdefault('SKIP_INTEGRATION', '0')
root = pathlib.Path('.').resolve(); sys.path.insert(0, str(root))
mods = [str(p.relative_to(root))[:-3].replace('/', '.').removesuffix('.__init__') for p in sorted((root/'sqlglot').rglob('*.py')) if p.name != '__main__.py']
mods += [str(p.relative_to(root))[:-3].replace('/', '.') for p in sorted((root/'tests').rglob('test_*.py'))]
bad = []
for m in mods:
    try: importlib.import_module(m)
    except BaseException as e: bad.append((m, repr(e)[:80]))
print('imported', len(mods), 'failed', bad, file=sys.stderr)
