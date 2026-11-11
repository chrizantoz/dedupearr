"""Mutation check: prove the verdict suite has teeth. Copies the code to a temp dir, breaks one rule on purpose, runs tests/verdicts.py there
and demands a FAILURE. If a mutant survives, the suite is decorative for that rule.
Run: python tests/mutation_check.py [all | index]   (CI rotates one mutant per day; 'all' runs every mutant)"""
import datetime, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTANTS = [   # (name, exact text in dedupearr.py, replacement that silently breaks the rule)
    ("hardlinks never recognised", 'if "library" in kinds and "download" in kinds:', 'if False and "library" in kinds and "download" in kinds:'),
    ("identical copies never confirmed", "if s1 == s2 and (f1 is None or f2 is None or f1 == f2):", "if False and s1 == s2:"),
    ("header-differs duplicates ignored", 'if r["size"] > 3 * SAMPLE and s1.split(":")[1:] == s2.split(":")[1:]:', 'if False:'),
    ("recycle rule never expires", "old = days > RECYCLE_DAYS", "old = days > 999999"),
    ("waste never counted", 'r["size"] if code in ("DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED") else 0', '0'),
    ("recycle bin mistaken for library", 'if any(p in RECYCLE_HINTS for p in parts): return "recycle"', 'if any(p in RECYCLE_HINTS for p in parts): return "library"'),
    ("vanished files kept forever", 'if r["path"] not in seen: c.execute("DELETE FROM verdict WHERE id=?"', 'if False: c.execute("DELETE FROM verdict WHERE id=?"'),
    ("library size double-counts hardlinks", "GROUP BY dev, ino", "GROUP BY path"),
]
arg = sys.argv[1] if len(sys.argv) > 1 else str(datetime.date.today().timetuple().tm_yday % len(MUTANTS))
todo = MUTANTS if arg == "all" else [MUTANTS[int(arg) % len(MUTANTS)]]
src = open(os.path.join(ROOT, "dedupearr.py")).read(); bad = 0
for name, old, new in todo:
    if old not in src:   # the vanished-files mutant anchors on a line that uses 'path', not 'id'
        alt = old.replace("id=?", "path=?")
        if alt in src: old, new = alt, new.replace("id=?", "path=?")
        else: print(f"FAIL mutant '{name}': anchor text not found in dedupearr.py (update the mutant)"); bad += 1; continue
    tmp = tempfile.mkdtemp(prefix="dedupearr-mut-")
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(tmp, "tests"), ignore=shutil.ignore_patterns("__pycache__"))
    open(os.path.join(tmp, "dedupearr.py"), "w").write(src.replace(old, new, 1))
    p = subprocess.run([sys.executable, os.path.join(tmp, "tests", "verdicts.py")], capture_output=True, text=True, timeout=600)
    caught = p.returncode != 0
    print(f"{'PASS' if caught else 'FAIL'} mutant '{name}': {'caught by the suite' if caught else 'SURVIVED - the suite did not notice'}  ({[l for l in p.stdout.splitlines() if l.startswith('FAIL')][:2]})")
    bad += 0 if caught else 1; shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(todo) - bad} of {len(todo)} mutants caught"); sys.exit(1 if bad else 0)
