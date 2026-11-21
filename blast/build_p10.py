"""Build-time caches for strategy p10. The scorer does not charge this step.

Writes idx_cache.pkl and preset_cache.pkl next to the maps. Both must be world-readable.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

ROOT = Path("/app")
CODE = ROOT / "research" / "ethan-p10" / "code"
ART = Path(os.environ.get("BLAST_P10_ART", "/opt/p10maps"))
SRC = Path(os.environ.get("BLAST_SQLGLOT_SRC", "/opt/sqlglot"))


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("BR_ARTIFACTS", str(ART))
    os.environ.setdefault("BR_IDXCACHE", str(ART / "idx_cache.pkl"))
    os.environ.setdefault("BR_IMPDERIV", "1")
    os.environ.setdefault("BR_LAMBDA", "1")
    sys.path.insert(0, str(CODE))
    sys.argv = ["eval_baselines.py", str(ROOT), str(SRC), str(ART)]
    # tests.json is mounted at run time; the index does not need it
    empty = ART / "empty_tests.json"
    empty.write_text('{"tests":[]}', encoding="utf-8")
    os.environ["BR_TESTS"] = str(empty)
    import eval_baselines as eb
    import importtime

    eb.load_index(importtime)
    print("index", ART / "idx_cache.pkl", flush=True)

    import fastprobe as fp

    classes = []
    for path in sorted((SRC / "tests").rglob("test_*.py")):
        rel = path.relative_to(SRC).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        module = rel[:-3].replace("/", ".")
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                isinstance(child, ast.FunctionDef) and child.name == "setUpClass" for child in node.body
            ):
                classes.append((module, node.name))
    mods = sorted({module for module, _name in classes})
    fp.warm(str(SRC), mods)
    fp.preset(classes)
    print("presets", fp.save_presets(str(ART / "preset_cache.pkl")), flush=True)
    _build_extra()


def _build_extra() -> None:
    """astindex.pkl (old-file AST facts) and keyidx/ (key-level dict read map). Separate processes: the key map
    must be recorded with a clean interpreter whose class-creation hook is installed before sqlglot is imported."""
    import subprocess

    py = sys.executable
    sys.path.insert(0, str(ROOT))
    from blast import select_p10

    select_p10.build_id_facts(SRC, ART / "idfacts.pkl")
    subprocess.run([py, str(CODE / "build_astindex.py"), str(SRC), str(ART / "astindex.pkl")], check=True)
    tests_json = os.environ.get("BLAST_TESTS_JSON", str(ROOT / "sample" / "tests.json"))
    seeds = os.environ.get("BLAST_KEYMAP_SEEDS", "0 12345").split()
    workers = os.environ.get("BLAST_KEYMAP_WORKERS", str(os.cpu_count() or 4))
    maps = []
    for seed in seeds:
        out = f"/tmp/keymap_{seed}.pkl"
        env = dict(os.environ, PYTHONHASHSEED=seed)
        subprocess.run([py, str(CODE / "build_keymap.py"), str(SRC), tests_json, out, workers], check=True, env=env)
        maps.append(out)
    subprocess.run([py, str(CODE / "build_keyidx.py"), str(ART / "keyidx"), *maps], check=True)
    for out in maps:
        os.remove(out)


if __name__ == "__main__":
    main()
