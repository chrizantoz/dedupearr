"""Phase 3 gate: a 'fix' in dry run must leave the disk and the clients exactly as they were, every fixable row must have a plan in plain words,
a fix never runs during a scan, and every attempt is written to the actions log. Run: python tests/fixes.py   Exit 1 on failure."""
import os, sys, json, time, tempfile, socket, hashlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-fix-"); DATA = os.path.join(TMP, "data")
import fake_disk, mock_clients  # noqa: E402
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); M = f"http://127.0.0.1:{port}"
now = time.time(); P = fake_disk.build(DATA, now); P.update(mock_clients.extra_files(DATA, fake_disk.blob)); world = mock_clients.world_for(P, DATA); srv = mock_clients.start(world, port)
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100", CLIENT_TIMEOUT="5", FIX_MODE="dryrun", **mock_clients.env_for(M))
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:240]}]"))
def snapshot(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f); st = os.lstat(p); out[p] = (st.st_ino, st.st_size, st.st_nlink, hashlib.blake2b(open(p, "rb").read(), digest_size=8).hexdigest() if os.path.isfile(p) else "")
    return out
d.scan(now=now); c = d.db(); S = d.state(c); by = {i["path"]: i for i in S["items"]}
fixable = [i for i in S["items"] if i["fixable"]]
check("fixable rows exist and all of them need a decision", fixable and all(i["needs_you"] for i in fixable), len(fixable))
check("a row still seeding is not fixable, an orphan is", not by[P["hdr_dl"]]["fixable"] and by[P["look_dl"]]["fixable"] and by[P["look_dl"]]["code"] == "ORPHAN")
before = snapshot(DATA); client_before = json.dumps(world.get("deluge")) + json.dumps(world.get("nzbget"))
plain = True
for i in fixable:
    code, obj = d.ui_fix(c, i["path"])
    ok = code == 200 and obj.get("dry_run") is True and obj["steps"] and all(st.endswith(".") and "/data/" not in st.replace(DATA, "/data") or "holding bin" in st for st in obj["steps"])
    if not ok: plain = False; print("   ", code, obj)
check("every fixable row gets a dry-run plan in full sentences", plain)
check("the dry run changed nothing on disk (same inodes, sizes, link counts, contents)", snapshot(DATA) == before)
check("the dry run told no client to remove anything", "_removed" not in world and "_nz_edits" not in world and json.dumps(world.get("deluge")) + json.dumps(world.get("nzbget")) == client_before)
check("every attempt is in the actions log as a dry run", len(d.actions(c)) == len(fixable) and all(a["mode"] == "dryrun" for a in d.actions(c)), len(d.actions(c)))
code, obj = d.ui_fix(c, P["hdr_dl"]); check("a still-seeding copy is refused with the seeding sentence, not a code", code == 400 and "still seeding" in obj["error"], obj)
code, obj = d.ui_fix(c, P["real_lib"]); check("a fine file is refused in plain words", code == 400 and "Nothing to fix" in obj["error"], obj)
code, obj = d.ui_fix(c, "/nowhere/x.mkv"); check("an unknown file is refused", code == 404)
d.SCANNING["on"] = True; code, obj = d.ui_fix(c, fixable[0]["path"]); d.SCANNING["on"] = False
check("a fix during a scan is refused (never race the scan)", code == 409 and "scan is running" in obj["error"], obj)
rel = next((i for i in fixable if i["code"] == "DUPLICATE_COPY"), None)
if rel: code, obj = d.ui_fix(c, rel["path"]); check("the relink plan keeps the client seeding and ends with the undo window", obj["kind"] == "relink" and any("hardlink" in st for st in obj["steps"]) and "Undo" in obj["steps"][-1], obj.get("steps"))
lo = next((i for i in fixable if i["code"] == "LEFTOVER" and i["client"]), None)
if lo: code, obj = d.ui_fix(c, lo["path"]); check("a leftover's plan names the client and the holding bin", obj["kind"] == "remove" and any(lo["client"]["client"] in st for st in obj["steps"]) and any("holding bin" in st for st in obj["steps"]), obj.get("steps"))
a = d.about(c); check("About says fixes are a dry run", any("dry run" in h["text"] for h in a["health"]))
check("the page state carries the fix mode and the holding bin path", S["fix_mode"] == "dryrun" and S["holding_dir"].startswith(DATA))
srv.shutdown(); srv.server_close(); c.close(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
