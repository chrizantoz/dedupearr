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
    ("morning message counts ignored files", 'needs = [v for v in s["items"] if v["needs_you"]]; back = s["reclaim_bytes"]', 'needs = list(s["items"]); back = s["reclaim_bytes"]'),
    ("morning message sent from the public demo", "    if not NTFY_URL or SANDBOX: return False\n    headers", "    if not NTFY_URL: return False\n    headers"),
    ("morning message hides trouble", 'lines.append("Trouble: " + "; ".join(trouble) if trouble else "No errors since yesterday")', 'lines.append("No errors since yesterday")'),
    ("Auditarr trash counted as library", 'or any(part in AUDITARR_TRASH_NAMES for part in path.split("/")[:-1])', 'or False'),
    ("Auditarr trash offered for emptying", 'elif r["path"] in atrash: verdicts[r["path"]] = ("AUDITARR_BIN"', 'elif False: verdicts[r["path"]] = ("AUDITARR_BIN"'),
    ("Auditarr change never explained", 'if (a := by_auditarr(l["path"])): extra_a[r["path"]] = a;', 'if False and (a := by_auditarr(l["path"])): extra_a[r["path"]] = a;'),
    ("seeding rule always satisfied", 'return (r_ok or d_ok) if rule_for(ci) == "either" else (r_ok and d_ok)', 'return True'),
    ("seeding rule ignores public torrents", 'return "either" if (ci or {}).get("private") is False else "both"', 'return "both"'),
    ("same-title leftovers never noticed", 'if same_title(k, k2):', 'if False:'),
    ("recycle bin mistaken for library", 'if any(p in RECYCLE_HINTS for p in parts): return "recycle"', 'if any(p in RECYCLE_HINTS for p in parts): return "library"'),
    ("vanished files kept forever", 'if r["path"] not in seen: c.execute("DELETE FROM verdict WHERE id=?"', 'if False: c.execute("DELETE FROM verdict WHERE id=?"'),
    ("library size double-counts hardlinks", "GROUP BY dev, ino", "GROUP BY path"),
    ("holding bin moves a file that changed since the scan", 'if (st.st_dev, st.st_ino, st.st_size) != (f["dev"], f["ino"], f["size"]):', 'if False:'),
    ("holding bin purge ignores the days", 'AND at < ?", (now - HOLD_DAYS * 86400,)', 'AND at < ?", (now + 1e12,)'),
    ("Put back overwrites a new file", 'if os.path.lexists(r["orig"]):\n', 'if False:\n'),
    ("a download still coming in called seeding only", 'elif ci and not ci.get("finished") and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE"):', 'elif False:'),
    ("the arr's import queue ignored", 'elif (aq := queue_for(r["path"], queue)) and code in', 'elif (aq := None) and code in'),
    ("remove fix deletes the torrent's files", '"core.remove_torrent", [ci["id"], False]', '"core.remove_torrent", [ci["id"], True]'),
    ("remove fix trusts last night's scan", 'if not seeding_done(cur): return 409', 'if False: return 409'),
    ("remove fix ignores the torrent's other files", 'if others: return remove_pack(c, v, torrent, [path] + others, why, now)', 'if False: return 1'),
    ("remove fix takes a pack although one file is in no library", 'if odd: return [], 0,', 'if False: return [], 0,'),
    ("remove fix leaves half a pack in the bin when a later file cannot move", 'if code != 200: return back(f"one file of the torrent', 'if code != 200: return code, obj; back(f"one file of the torrent'),
    ("holding bin move ignores the lock Auditarr holds", 'if _lock_theirs(info, age): raise FileBusy(_lock_sentence(info))', 'if False: raise FileBusy(_lock_sentence(info))'),
    ("relink does not lock the library file", 'for p in (v["path"], v.get("twin") or ""): toks.append(', 'for p in (v["path"],): toks.append('),
    ("Put back ignores the lock Auditarr holds", 'try: tok = lock_take(r["orig"], "putting a file back from the holding bin") if r else None', 'try: tok = None'),
    ("relink trusts the scan and skips the byte comparison", 'if not same: return 409', 'if False: return 409'),
    ("relink leaves a half-done change when the link fails", 'try: os.link(twin, path)\n    except OSError as e: return undo(', 'try: os.link(twin, path)\n    except OSError as e: return 500, {"error": "x"}; undo('),
    ("Put back after a relink overwrites something new", '(so.st_ino, so.st_size) == (r["link_ino"], r["size"]) and so.st_nlink >= 2', 'True'),
    ("remove fix rides along with 'Move all'", 'not (kind == "remove" and ci and ci.get("client") in REMOVE_READERS)', 'True'),
    ("remove fix leaves the file in the bin when the client refuses", 'back, _ = restore_held(c, obj["held_id"])', 'back = 0'),
    ("failed usenet job called seeding only", 'elif is_usenet(ci) and ci.get("finished") and code in', 'elif False and is_usenet(ci) and ci.get("finished") and code in'),
    ("client reader: rTorrent ratio read as a whole number", '"ratio": _num(ratio) / 1000', '"ratio": _num(ratio)'),
    ("client reader: Transmission's own 'finished' trusted", 'done = _num(t.get("percentDone")) >= 1 and _num(t.get("leftUntilDone")) == 0', 'done = t.get("isFinished") is True'),
    ("client reader: a switched-off client reported as broken", 'if name in soft:   # switched off in the arr', 'if False:   # switched off in the arr'),
    ("client discovery from the arrs ignored", 'clients = {}; found = read_arr_clients();', 'clients = {}; found = [];'),
    ("remove fix moves a file rTorrent still holds", 'if no_remove_call(ci): return 409, {"error": no_remove_sentence(ci)}', 'if False: return 409, {}'),
    ("extras called something to decide", 'if code == "DOWNLOAD_UNMATCHED" and not (is_usenet(ci) and ci.get("finished")) and (xk := extra_kind(r["path"])):', 'if False and (xk := ""):'),
    ("settings: the container's value no longer wins", 'if _envset(name) or (SETTABLE[name][0].startswith("conn:") and group_locked(name)): return', 'if False: return'),
    ("settings: changed from outside the home network without a sign-in", 'if direct_local(ip, headers): return True, ""', 'return True, ""'),
    ("settings: a new address keeps the old password", 'if new.get(u, "") != SETTINGS.get(u, "") and SETTINGS.get(u):', 'if False:'),
    ("settings: FIX_MODE settable from the page", 'unknown = [k for k in list(changes) + reset if k not in SETTABLE]', 'unknown = [k for k in list(changes) + reset if k not in SETTABLE and k != "FIX_MODE"]'),
    ("shared-mount test: two mounts called one", 'if set(lm) & set(dm) and same_dev:', 'if True:'),
    ("remove fix offered for a client it cannot tell", 'if v["fix_kind"] == "remove" and no_remove_call(ci):', 'if False:'),
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
    suites = [os.path.join(tmp, "tests", "verdicts.py")] + ([os.path.join(tmp, "tests", "clients.py")] if any(w in name for w in ("seeding", "import", "usenet", "client")) else []) + ([os.path.join(tmp, "tests", "fixes.py")] if "holding" in name or "Put back" in name or "remove fix" in name or "relink" in name else []) + ([os.path.join(tmp, "tests", "digest.py")] if "morning message" in name else []) + ([os.path.join(tmp, "tests", "auditarr_link.py")] if "Auditarr" in name else []) + ([os.path.join(tmp, "tests", "settings.py")] if name.startswith(("settings", "shared-mount")) else [])
    caught = any(subprocess.run([sys.executable, t], capture_output=True, text=True, timeout=600).returncode != 0 for t in suites)
    p = subprocess.run([sys.executable, suites[-1]], capture_output=True, text=True, timeout=600) if not caught else subprocess.CompletedProcess([], 1, "", "")
    print(f"{'PASS' if caught else 'FAIL'} mutant '{name}': {'caught by the suite' if caught else 'SURVIVED - the suite did not notice'}  ({[l for l in p.stdout.splitlines() if l.startswith('FAIL')][:2]})")
    bad += 0 if caught else 1; shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(todo) - bad} of {len(todo)} mutants caught"); sys.exit(1 if bad else 0)
