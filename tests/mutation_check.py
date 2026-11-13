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
    ("waste never counted", 'r["size"] if code in ("DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN") else 0', '0'),
    ("seeding rule always satisfied", 'return (r_ok or d_ok) if rule_for(ci) == "either" else (r_ok and d_ok)', 'return True'),
    ("seeding rule ignores public torrents", 'return "either" if (ci or {}).get("private") is False else "both"', 'return "both"'),
    ("same-title leftovers never noticed", 'if same_title(k, k2):', 'if False:'),
    ("recycle bin mistaken for library", 'if any(p in RECYCLE_HINTS for p in parts): return "recycle"', 'if any(p in RECYCLE_HINTS for p in parts): return "library"'),
    ("vanished files kept forever", 'if r["path"] not in seen: c.execute("DELETE FROM verdict WHERE id=?"', 'if False: c.execute("DELETE FROM verdict WHERE id=?"'),
    ("library size double-counts hardlinks", "GROUP BY dev, ino", "GROUP BY path"),
    ("holding bin moves a file that changed since the scan", 'if (st.st_dev, st.st_ino, st.st_size) != (f["dev"], f["ino"], f["size"]):', 'if False:'),
    ("holding bin purge ignores the days", 'AND at < ?", (now - HOLD_DAYS * 86400,)', 'AND at < ?", (now + 1e12,)'),
    ("Put back overwrites a new file", 'if os.path.lexists(r["orig"]): return 409', 'if False: return 409'),
    ("a download still coming in called seeding only", 'elif ci and not ci.get("finished") and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE"):', 'elif False:'),
    ("the arr's import queue ignored", 'elif (aq := queue_for(r["path"], queue)) and code in', 'elif (aq := None) and code in'),
    ("failed usenet job called seeding only", 'elif ci and ci.get("client") == "NZBGet" and ci.get("finished") and code in', 'elif False and ci and ci.get("client") == "NZBGet" and ci.get("finished") and code in'),
]
arg = sys.argv[1] if len(sys.argv) > 1 else str(datetime.date.today().timetuple().tm_yday % len(MUTANTS))
todo = MUTANTS if arg == "all" else [] if arg == "anchors" else [MUTANTS[int(arg) % len(MUTANTS)]]
src = open(os.path.join(ROOT, "dedupearr.py")).read(); bad = 0
if arg == "anchors":   # every run: each mutant must still find its line (2026-11-12: a security fix moved one, and the one-a-day rotation would not have said so for weeks)
    miss = [n for n, old, _ in MUTANTS if old not in src and old.replace("id=?", "path=?") not in src]
    print(f"{len(MUTANTS) - len(miss)} of {len(MUTANTS)} mutant anchors found" + (": missing " + ", ".join(miss) if miss else "")); sys.exit(1 if miss else 0)
for name, old, new in todo:
    if old not in src:   # the vanished-files mutant anchors on a line that uses 'path', not 'id'
        alt = old.replace("id=?", "path=?")
        if alt in src: old, new = alt, new.replace("id=?", "path=?")
        else: print(f"FAIL mutant '{name}': anchor text not found in dedupearr.py (update the mutant)"); bad += 1; continue
    tmp = tempfile.mkdtemp(prefix="dedupearr-mut-")
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(tmp, "tests"), ignore=shutil.ignore_patterns("__pycache__"))
    open(os.path.join(tmp, "dedupearr.py"), "w").write(src.replace(old, new, 1))
    suites = [os.path.join(tmp, "tests", "verdicts.py")] + ([os.path.join(tmp, "tests", "clients.py")] if any(w in name for w in ("seeding", "import", "usenet")) else []) + ([os.path.join(tmp, "tests", "fixes.py")] if "holding" in name or "Put back" in name else [])
    caught = any(subprocess.run([sys.executable, t], capture_output=True, text=True, timeout=600).returncode != 0 for t in suites)
    p = subprocess.run([sys.executable, suites[-1]], capture_output=True, text=True, timeout=600) if not caught else subprocess.CompletedProcess([], 1, "", "")
    print(f"{'PASS' if caught else 'FAIL'} mutant '{name}': {'caught by the suite' if caught else 'SURVIVED - the suite did not notice'}  ({[l for l in p.stdout.splitlines() if l.startswith('FAIL')][:2]})")
    bad += 0 if caught else 1; shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(todo) - bad} of {len(todo)} mutants caught"); sys.exit(1 if bad else 0)
