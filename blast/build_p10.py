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
    """Optional speed artifacts; the selector runs without any of them (each lookup falls back).
    idfacts.pkl (class facts for the identity rule), astindex.pkl (old-file AST facts) and keyidx/ (key-level dict
    read map). keyidx ships prebuilt as research/ethan-p10/code/keyidx.tgz (recorded from the pinned sqlglot with
    build_keymap.py, seeds 0 and 12345, merged by build_keyidx.py), so the build does not rerun every test.
    Set BLAST_KEYMAP_REBUILD=1 to record it again here (about 3-4 minutes on 4 cores)."""
    import shutil
    import subprocess
    import tarfile

    py = sys.executable
    sys.path.insert(0, str(ROOT))
    try:
        from blast import select_p10

        select_p10.build_id_facts(SRC, ART / "idfacts.pkl")
    except Exception as error:  # the identity rule parses at run time instead
        print("idfacts skipped:", error, flush=True)
    try:
        subprocess.run([py, str(CODE / "build_astindex.py"), str(SRC), str(ART / "astindex.pkl")], check=True)
    except Exception as error:
        print("astindex skipped:", error, flush=True)
    final = ART / "keyidx"
    tmp = ART / "keyidx.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        packed = CODE / "keyidx.tgz"
        if packed.is_file() and os.environ.get("BLAST_KEYMAP_REBUILD", "0") != "1":
            with tarfile.open(packed) as tar:
                tar.extractall(tmp, filter="data")
            inner = tmp / "keyidx"
            if inner.is_dir():
                tmp = inner
        else:
            tests_json = os.environ.get("BLAST_TESTS_JSON", str(ROOT / "sample" / "tests.json"))
            seeds = os.environ.get("BLAST_KEYMAP_SEEDS", "0 12345").split()
            workers = os.environ.get("BLAST_KEYMAP_WORKERS", str(os.cpu_count() or 4))
            maps = []
            for seed in seeds:
                out = f"/tmp/keymap_{seed}.pkl"
                env = dict(os.environ, PYTHONHASHSEED=seed)
                subprocess.run([py, str(CODE / "build_keymap.py"), str(SRC), tests_json, out, workers], check=True, env=env)
                maps.append(out)
            subprocess.run([py, str(CODE / "build_keyidx.py"), str(tmp), *maps], check=True)
            for out in maps:
                os.remove(out)
        if not (tmp / "_meta.pkl").is_file():
            raise RuntimeError("keyidx has no _meta.pkl")
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
        os.chmod(final, 0o755)
        for path in final.iterdir():
            os.chmod(path, 0o644)
        shutil.rmtree(ART / "keyidx.tmp", ignore_errors=True)
        print("keyidx", final, len(list(final.iterdir())), "files", flush=True)
    except Exception as error:  # dict narrowing turns itself off without _meta.pkl
        print("keyidx skipped:", error, flush=True)
        shutil.rmtree(ART / "keyidx.tmp", ignore_errors=True)


if __name__ == "__main__":
    main()
