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
# ---- P3.1: the holding bin. FIX_MODE=on moves an orphan into the bin (nothing deleted), Put back undoes it, the purge waits HOLD_DAYS
d.FIX_MODE = "on"; d.scan(now=now); c = d.db(); orphan = P["look_dl"]; st0 = os.lstat(orphan)
code, obj = d.ui_fix(c, orphan); held = d.holding(c)["items"]
check("on: an orphan moves into the holding bin, same inode, nothing deleted", code == 200 and not obj["dry_run"] and not os.path.exists(orphan) and os.lstat(obj["dest"]).st_ino == st0.st_ino and obj["dest"].startswith(d.HOLDING_DIR + os.sep), obj)
check("on: the bin lists it with the days left, the row leaves the page", len(held) == 1 and held[0]["orig"] == orphan and held[0]["days_left"] > d.HOLD_DAYS - 0.1 and orphan not in {i["path"] for i in d.state(c)["items"]}, held)
check("on: the move is in the actions log as done, with its undo", any(a["mode"] == "on" and a["path"] == orphan and a["undo"] for a in d.actions(c)))
d.scan(now=now); check("the next scan does not list the holding bin as library or downloads", not any(i["path"].startswith(d.HOLDING_DIR) for i in d.state(c)["items"]))
rel = next((i for i in d.state(c)["items"] if i["fixable"] and i["fix_kind"] == "relink"), None)
if rel: code, obj = d.ui_fix(c, rel["path"]); check("on: a fix kind not switched on yet still only runs as a dry run", code == 200 and obj["dry_run"] and os.path.exists(rel["path"]) and "not switched on yet" in obj["note"], obj)
open(orphan, "wb").write(b"new file at the old place")
code, obj = d.restore_held(c, held[0]["id"]); check("Put back refuses when something new sits at the old place", code == 409 and "old place" in obj["error"] and os.path.exists(obj.get("dest") or d.hold_target(orphan, now)) is not None, obj)
os.remove(orphan); code, obj = d.restore_held(c, held[0]["id"])
check("Put back returns the same file to its old place and empties the bin", code == 200 and os.lstat(orphan).st_ino == st0.st_ino and d.holding(c)["items"] == [] and not os.path.exists(os.path.join(d.HOLDING_DIR, time.strftime("%Y-%m-%d", time.localtime(now)))), obj)
check("Put back twice is refused", d.restore_held(c, held[0]["id"])[0] == 409)
d.scan(now=now); code, obj = d.ui_fix(c, orphan); hid = obj.get("held_id")
open(orphan + ".tmp", "wb").close(); os.remove(orphan + ".tmp")
n, b = d.purge_holding(c, now + (d.HOLD_DAYS - 1) * 86400); check("the purge keeps a file whose days have not run out", n == 0 and os.path.exists(obj["dest"]))
n, b = d.purge_holding(c, now + (d.HOLD_DAYS + 1) * 86400); check("after its days the purge deletes it and logs it", n == 1 and not os.path.exists(obj["dest"]) and any(a["kind"] == "purge" for a in d.actions(c)), (n, b))
check("a purged file cannot be put back", d.restore_held(c, hid)[0] == 409)
for k in ("stray1", "stray2"): fake_disk.blob(os.path.join(DATA, "torrents", "tv", f"Nobody.Knows.{k}.mkv"), 2, "unique-" + k)
d.scan(now=now); bins = [i for i in d.state(c)["items"] if i["fixable"] and i["fix_kind"] == "bin"]
check("the test world has orphans left to move", len(bins) >= 2, [i["path"] for i in bins])
chg = bins[0]
with open(chg["path"], "ab") as fh: fh.write(b"grown since the scan")
code, obj = d.ui_fix(c, chg["path"]); check("a file that changed since the scan is not moved", code == 409 and os.path.exists(chg["path"]) and "changed since" in obj["error"], obj)
ro = os.path.join(TMP, "ro-hold"); os.makedirs(ro); os.chmod(ro, 0o500); old = d.HOLDING_DIR; d.HOLDING_DIR = os.path.join(ro, "bin"); d.scan(now=now)
tgt = next((i for i in d.state(c)["items"] if i["fixable"] and i["fix_kind"] == "bin"), None)
check("a read-only test target exists", tgt is not None)
if tgt and os.geteuid() != 0:
    code, obj = d.ui_fix(c, tgt["path"]); check("a read-only place says so in plain words and moves nothing", code == 409 and "read-only" in obj["error"] and os.path.exists(tgt["path"]), obj)
os.chmod(ro, 0o700); d.HOLDING_DIR = old; d.FIX_MODE = "dryrun"
# CodeQL #3-#8 (2026-11-12): the holding bin acts only on a file the last scan recorded, before it touches the disk
stray = os.path.join(DATA, "not-scanned.mkv"); open(stray, "wb").write(b"x" * 2048); d.FIX_MODE = "on"
code_, obj_ = d.hold_file(c, stray, "test", "test"); check("a file inside the folders that the scan never recorded is refused and stays put", code_ == 409 and os.path.exists(stray) and "no record" in obj_["error"], (code_, obj_))
os.remove(stray); d.FIX_MODE = "dryrun"
# ---- P3.2 (2026-11-14): remove a leftover or second copy through the client. The file goes to the holding bin, the client forgets the torrent and keeps its files
d.FIX_MODE = "on"; d.scan(now=now); scary = P["other_dl"]; T = next(t for t in world["deluge"] if t["id"] == "scary"); T0 = dict(T); ino = os.lstat(scary).st_ino
row = lambda p: next((i for i in d.state(c)["items"] if i["path"] == p), None)
check("the test world has a download whose seeding is done and whose torrent is still in the client", row(scary) and row(scary)["fix_kind"] == "remove" and row(scary)["client"]["id"] == "scary", row(scary))
T.update(ratio=0.1, seeding_time=86400, private=True); code, obj = d.ui_fix(c, scary)
check("remove: the client is asked again first; a torrent no longer past the rule is left alone", code == 409 and "seeding rule" in obj["error"] and os.path.exists(scary) and "_removed" not in world, (code, obj))
T.update(T0); T.pop("private", None); big = fake_disk.blob(os.path.join(os.path.dirname(scary), "Scary.Movie.2026.Extras.mkv"), 2, "scary-extras"); T["files"] = T0["files"] + [os.path.basename(big)]; code, obj = d.ui_fix(c, scary)
check("remove: a torrent that holds another large file is left alone (it would stop seeding that one too)", code == 409 and "other large file" in obj["error"] and os.path.exists(scary) and "_removed" not in world, (code, obj))
os.remove(big); open(big[:-4] + ".nfo", "w").write("release notes"); T["files"] = T0["files"] + [os.path.basename(big)[:-4] + ".nfo"]
world["_remove_fails"] = True; code, obj = d.ui_fix(c, scary)
check("remove: when the client refuses, the file goes straight back and nothing is left in the bin", code == 502 and "back in its place" in obj["error"] and os.lstat(scary).st_ino == ino and d.holding(c)["items"] == [] and any(t["id"] == "scary" for t in world["deluge"]), (code, obj))
world.pop("_remove_fails"); d.scan(now=now); url = d.CLIENTS["Deluge"]["url"]; d.CLIENTS["Deluge"]["url"] = "http://127.0.0.1:9/deluge"; code, obj = d.ui_fix(c, scary); d.CLIENTS["Deluge"]["url"] = url
check("remove: a client that does not answer means nothing is changed", code == 502 and "did not answer" in obj["error"] and os.path.exists(scary) and d.holding(c)["items"] == [], (code, obj))
code, obj = d.ui_fix(c, scary); held = d.holding(c)["items"]
check("remove: the file moves to the holding bin (same file, nothing deleted) and the client forgets the torrent but keeps its files", code == 200 and not obj["dry_run"] and not os.path.exists(scary) and os.lstat(obj["dest"]).st_ino == ino and world.get("_removed") == [("scary", False)] and not any(t["id"] == "scary" for t in world["deluge"]), (code, obj, world.get("_removed")))
check("remove: the bin says the torrent is gone and the note says what Put back returns", len(held) == 1 and "torrent" in held[0]["note"] and "not the torrent" in obj["note"], (held, obj.get("note")))
check("remove: both steps are in the actions log", sum(1 for a in d.actions(c) if a["mode"] == "on" and a["path"] == scary and a["kind"] == "remove" and "refused" not in a["result"] and "would not" not in a["result"]) >= 2)
code, obj = d.restore_held(c, held[0]["id"]); check("remove: Put back returns the same file to its old place", code == 200 and os.lstat(scary).st_ino == ino, obj)
world["deluge"].append(dict(T0)); d.scan(now=now); world["deluge"] = [t for t in world["deluge"] if t["id"] != "scary"]; n_rm = len(world["_removed"]); code, obj = d.ui_fix(c, scary)
check("remove: a torrent already gone from the client needs no client call; the file still goes to the bin", code == 200 and not os.path.exists(scary) and len(world["_removed"]) == n_rm, (code, obj))
for key, who in (("unmatched", "no client"), ("usenet", "NZBGet")):
    rw = row(P[key]); code, obj = d.ui_fix(c, P[key]) if rw and rw["fix_kind"] == "remove" else (0, rw)
    check(f"remove: a leftover with {who} goes to the holding bin and no client is told anything", code == 200 and not os.path.exists(P[key]) and len(world["_removed"]) == n_rm and "_nz_edits" not in world, (code, obj))
d.client_remove({"client": "qBittorrent", "id": "abc123", "name": "x"}); check("remove: qBittorrent is told to keep the files too", world["_removed"][-1] == ("abc123", "false"), world["_removed"][-1])
# ---- 'Move all shown' (2026-11-14): many plain moves in one press, each through the same checks; a torrent removal never rides along
hid = next(h["id"] for h in d.holding(c)["items"] if h["orig"] == scary); d.restore_held(c, hid); world["deluge"].append(dict(T0))
B = [os.path.join(DATA, "torrents", "tv", f"Nobody.Knows.bulk{k}.mkv") for k in (1, 2, 3, 4)]
for k, b in enumerate(B): fake_disk.blob(b, 2, f"unique-bulk{k}")
d.scan(now=now); c.execute("INSERT INTO ignored(path, at) VALUES(?, ?)", (B[3], now)); c.commit()
check("move all: plain moves are marked for it, a torrent removal is not", all(row(b)["bulk"] for b in B[:3]) and row(scary)["fixable"] and not row(scary)["bulk"] and not row(B[3])["bulk"], [row(b)["bulk"] for b in B] + [row(scary)["bulk"]])
with open(B[2], "ab") as fh: fh.write(b"grown since the scan")
n_rm = len(world["_removed"]); ask = B + [scary, "/data/never-scanned.mkv", B[0]]
d.FIX_MODE = "dryrun"; code, obj = d.ui_fix_all(c, ask); check("move all: refused while fixes are a dry run, nothing moves", code == 403 and all(os.path.exists(b) for b in B), (code, obj)); d.FIX_MODE = "on"
d.SCANNING["on"] = True; code, obj = d.ui_fix_all(c, ask); d.SCANNING["on"] = False; check("move all: refused during a scan", code == 409 and os.path.exists(B[0]), (code, obj))
code, obj = d.ui_fix_all(c, ["/data/x.mkv"] * 0 + [f"/data/{i}.mkv" for i in range(d.BULK_MAX + 1)]); check("move all: a list that is too long is refused whole", code == 400 and "narrow" in obj["error"], (code, obj))
code, obj = d.ui_fix_all(c, ask); why = {k["path"]: k["error"] for k in obj.get("skipped", [])}
check("move all: the plain moves go to the holding bin and the answer counts them", code == 200 and obj["done"] == 2 and not os.path.exists(B[0]) and not os.path.exists(B[1]) and sum(1 for h in d.holding(c)["items"] if h["orig"] in B) == 2, (code, obj))
check("move all: a file that changed is left alone and named, the rest still went", os.path.exists(B[2]) and "changed since" in why.get(B[2], ""), why)
check("move all: a row that removes a torrent is left alone and no client is told anything", os.path.exists(scary) and "own row" in why.get(scary, "") and len(world["_removed"]) == n_rm, why)
check("move all: an ignored file and a file the scan never saw are left alone", os.path.exists(B[3]) and "ignore" in why.get(B[3], "") and "not in the last scan" in why.get("/data/never-scanned.mkv", ""), why)
check("move all: the note says how many moved and how many were left, in words", "Moved 2 files" in obj["note"] and "4 were left alone" in obj["note"], obj["note"])
d.FIX_MODE = "dryrun"
srv.shutdown(); srv.server_close(); c.close(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
