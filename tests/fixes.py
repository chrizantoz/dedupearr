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
d.LIVE_KINDS.discard("relink")
if rel: code, obj = d.ui_fix(c, rel["path"]); d.LIVE_KINDS.add("relink"); check("on: a fix kind not switched on yet still only runs as a dry run", code == 200 and obj["dry_run"] and os.path.exists(rel["path"]) and "not switched on yet" in obj["note"], obj)
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
check("remove: a torrent that holds another large file the scan never judged is left alone", code == 409 and "no verdict" in obj["error"] and os.path.exists(scary) and "_removed" not in world, (code, obj))
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
# P2.3 (2026-11-18): rTorrent is read, but Dedupearr cannot remove torrents from it yet, so its leftover keeps its file where rTorrent expects it
rl = row(P["rt_left"]); vrl = dict(c.execute("SELECT * FROM verdict WHERE path=?", (P["rt_left"],)).fetchone())
code, obj = d.ui_fix(c, P["rt_left"]); code2, obj2 = d.remove_fix(c, vrl, ["x"])
check("remove: an rTorrent leftover past the rule has no button, the fix is refused in words and the file stays where rTorrent expects it",
      rl and rl["code"] == "LEFTOVER" and not rl["fixable"] and code == 400 and code2 == 409 and "cannot remove torrents" in obj2.get("error", "") and os.path.exists(P["rt_left"]) and "_writes" not in world, (rl and rl["fixable"], code, code2, obj2))
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
# ---- P3.3 (2026-11-14): relink an identical copy. Compare every byte, copy to the holding bin, hardlink in its place; the client keeps seeding and is told nothing
dl, lib = P["dup_dl"], P["dup_lib"]; TD = next(t for t in world["deluge"] if t["id"] == "dup1"); TD.update(ratio=0.01, seeding_time=3600, private=True); d.scan(now=now); n_rm = len(world["_removed"])
r0 = row(dl); ino_dl, ino_lib = os.lstat(dl).st_ino, os.lstat(lib).st_ino
check("relink: an identical copy that still seeds can be relinked now (it keeps seeding), and never rides along with 'Move all'", r0 and r0["fix_kind"] == "relink" and r0["fixable"] and not r0["removable"] and not r0["bulk"] and ino_dl != ino_lib, r0)
vrow = dict(c.execute("SELECT * FROM verdict WHERE path=?", (dl,)).fetchone()); vdl = dict(vrow, client=json.dumps(dict(json.loads(vrow["client"]), finished=False)))
code, obj = d.relink_fix(c, vdl, ["x"]); check("relink: a file the client is still downloading is left alone", code == 409 and "still downloading" in obj["error"] and os.lstat(dl).st_ino == ino_dl, (code, obj))
check("relink: the plan for a file still downloading says so instead of offering steps", d.plan_fix(vdl)[0] is None and "still downloading" in d.plan_fix(vdl)[1][0])
with open(lib, "r+b") as fh: fh.seek(2 * 1024 * 1024 + 7); keep = fh.read(1); fh.seek(-1, 1); fh.write(bytes([keep[0] ^ 1]))
code, obj = d.ui_fix(c, dl); check("relink: one different byte in the middle and nothing is changed (no checksum from the scan is trusted)", code == 409 and "not identical" in obj["error"] and os.lstat(dl).st_ino == ino_dl and os.lstat(lib).st_nlink == 1 and not any(h["orig"] == dl for h in d.holding(c)["items"]), (code, obj))
with open(lib, "r+b") as fh: fh.seek(2 * 1024 * 1024 + 7); fh.write(keep)
_link = os.link
def _nolink(a, b): raise OSError(18, "Invalid cross-device link")
os.link = _nolink; code, obj = d.ui_fix(c, dl); os.link = _link
check("relink: when the hardlink cannot be made, the copy goes straight back and the bin stays empty of it", code == 500 and "back in its place" in obj["error"] and os.lstat(dl).st_ino == ino_dl and not any(h["orig"] == dl for h in d.holding(c)["items"]), (code, obj))
d.scan(now=now); code, obj = d.ui_fix(c, dl); held = [h for h in d.holding(c)["items"] if h["orig"] == dl]
check("relink: both names are one file, the old copy waits in the holding bin, nothing was deleted", code == 200 and os.lstat(dl).st_ino == ino_lib and os.lstat(lib).st_nlink == 2 and len(held) == 1 and os.lstat(obj["dest"]).st_ino == ino_dl, (code, obj))
check("relink: the client is told nothing and the note says it keeps seeding", len(world["_removed"]) == n_rm and "keeps seeding" in obj["note"] and "Put back" in obj["note"], obj.get("note"))
d.scan(now=now); check("relink: the next scan sees a real copy and a linked seed, and leaves the holding bin alone", row(dl)["code"] == "LINKED_SEED" and row(lib)["code"] == "REAL_COPY" and os.path.exists(obj["dest"]), (row(dl)["code"], row(lib)["code"]))
code, obj2 = d.restore_held(c, held[0]["id"]); check("relink: Put back swaps the old copy back in and the library file is a single name again", code == 200 and os.lstat(dl).st_ino == ino_dl and os.lstat(lib).st_nlink == 1 and os.lstat(lib).st_ino == ino_lib, (code, obj2))
d.scan(now=now); code, obj = d.ui_fix(c, dl); hid = next(h["id"] for h in d.holding(c)["items"] if h["orig"] == dl); os.remove(dl); open(dl, "wb").write(b"something new at the old name")
code, obj2 = d.restore_held(c, hid); check("relink: Put back never overwrites something new at the old name", code == 409 and "something new" in obj2["error"] and open(dl, "rb").read() == b"something new at the old name", (code, obj2))
os.remove(dl); os.link(lib, dl); os.remove(lib); code, obj2 = d.restore_held(c, hid)
check("relink: Put back never replaces the last name of the library file", code == 409 and os.lstat(dl).st_ino == ino_lib, (code, obj2))
os.link(dl, lib); code, obj2 = d.restore_held(c, hid); d.scan(now=now)
d.FIXING["path"] = "/busy"; code, obj = d.ui_fix(c, dl); sc = d.scan(now=now); d.FIXING["path"] = None
check("relink: while one long fix runs, another fix and a scan both wait", code == 409 and "another fix" in obj["error"] and sc is None, (code, obj, sc))
code, obj = d.ui_fix(c, dl, background=True); t0 = time.time()
while d.FIXING["path"] and time.time() - t0 < 20: time.sleep(0.05)
last = d.FIXING["last"] or {}
check("relink: from the page it runs beside the page and leaves its result for the row", code == 202 and obj.get("started") and last.get("ok") and last.get("path") == dl and d.FIXING["done"] == d.FIXING["total"] > 0 and os.lstat(dl).st_ino == ino_lib, (d.FIXING, code))
check("relink: the page state carries the progress and the last result", d.state(c)["fixing"]["last"]["path"] == dl and "relink" in d.state(c)["live_kinds"])
# ---- P3.4 (2026-11-14): an old recycle-bin file goes to the holding bin like everything else; no fix deletes directly
d.scan(now=now); bo = row(P["bin_old"]); bn = row(P["bin_new"]); ino_b = os.lstat(P["bin_old"]).st_ino
check("old bin: an old recycle-bin file can be moved and rides along with 'Move all'; a recent one has no fix", bo and bo["fix_kind"] == "empty" and bo["fixable"] and bo["bulk"] and not bn["fixable"], (bo, bn))
code, obj = d.ui_fix(c, P["bin_new"]); check("old bin: a file still inside your recycle rule is refused", code == 400 and os.path.exists(P["bin_new"]), (code, obj))
code, obj = d.ui_fix(c, P["bin_old"]); check("old bin: the file moves to the holding bin, same file, nothing deleted", code == 200 and not obj["dry_run"] and not os.path.exists(P["bin_old"]) and os.lstat(obj["dest"]).st_ino == ino_b and not any("for good" in s_ for s_ in obj["steps"]), (code, obj))
hid = next(h["id"] for h in d.holding(c)["items"] if h["orig"] == P["bin_old"]); code, obj = d.restore_held(c, hid)
check("old bin: Put back returns it to its recycle bin", code == 200 and os.lstat(P["bin_old"]).st_ino == ino_b, (code, obj))
# ---- P3.5 (2026-11-15): a torrent with several files (a season pack) goes as a whole or not at all
pk = os.path.join(DATA, "torrents", "tv", "Pack.Show.S01.1080p.BluRay-GRP"); lb = os.path.join(DATA, "media", "tv", "Pack Show", "Season 01")
E = {k: os.path.join(pk, f"Pack.Show.S01E0{k}.1080p.BluRay-GRP.mkv") for k in (1, 2, 3, 4)}; LB = {k: os.path.join(lb, f"Pack Show - S01E0{k}.mkv") for k in (1, 2, 3)}
for k in (1, 2): fake_disk.blob(E[k], 4 + k, f"pack-e{k}"); fake_disk.blob(LB[k], 4 + k, f"pack-e{k}")
with open(LB[1], "r+b") as fh: fh.seek(100); fh.write(b"tagged-in-place")   # E01: stored twice, header differs; E02: stored twice, identical
fake_disk.blob(LB[3], 3, "pack-e3"); os.link(LB[3], E[3])   # E03: a hardlink, costs nothing
fake_disk.blob(E[4], 7, "pack-e4-only-copy")   # E04: in no library at all
TP = {"id": "pack1", "name": "Pack.Show.S01.1080p.BluRay-GRP", "save_path": os.path.join(DATA, "torrents", "tv"), "files": [os.path.relpath(E[k], os.path.join(DATA, "torrents", "tv")) for k in (1, 2, 3, 4)], "state": "Seeding", "ratio": 2.5, "seeding_time": 30 * 86400, "label": "tv"}
world["deluge"].append(TP); d.scan(now=now); n_rm = len(world["_removed"]); codes = {k: row(E[k])["code"] for k in E}
check("pack: the test torrent holds a header copy, an identical copy, a hardlink and a file in no library", codes[1] == "DUPLICATE_HEADER" and codes[2] == "DUPLICATE_COPY" and codes[3] == "LINKED_SEED" and codes[4] not in d.PACK_HOLD | d.PACK_STAY, codes)
check("pack: while one file is in no library the row has no button and says what to decide first", not row(E[1])["fixable"] and "decide that one first" in row(E[1])["wait"], row(E[1]))
code, obj = d.ui_fix(c, E[1]); check("pack: pressing it anyway changes nothing and tells the client nothing", code == 409 and "decide that one first" in obj["error"] and all(os.path.exists(E[k]) for k in E) and len(world["_removed"]) == n_rm, (code, obj))
os.remove(E[4]); TP["files"] = TP["files"][:3]; d.scan(now=now); r1 = row(E[1])
check("pack: once every file is a spare copy or a hardlink, the row offers the whole torrent and counts its files", r1["fixable"] and r1["fix_kind"] == "remove" and r1["pack"] == {"files": 2, "stay": 1, "size_h": d.human(os.path.getsize(E[1]) + os.path.getsize(E[2]))} and not r1["bulk"], r1.get("pack"))
d.FIX_MODE = "dryrun"; code, obj = d.ui_fix(c, E[1]); d.FIX_MODE = "on"
check("pack: the dry-run plan names both files to move, the one that stays, and the space", code == 200 and obj["dry_run"] and any("all 2 files" in s_ for s_ in obj["steps"]) and any("linked to the library" in s_ for s_ in obj["steps"]) and obj["frees"] == os.path.getsize(E[1]) + os.path.getsize(E[2]) and all(os.path.exists(E[k]) for k in (1, 2, 3)), obj)
inos = {k: os.lstat(E[k]).st_ino for k in (1, 2, 3)}
world["_remove_fails"] = True; code, obj = d.ui_fix(c, E[1]); world.pop("_remove_fails")
check("pack: when the client refuses, every file goes straight back", code == 502 and "Every file is back" in obj["error"] and all(os.lstat(E[k]).st_ino == inos[k] for k in (1, 2, 3)) and not any(h["orig"] in E.values() for h in d.holding(c)["items"]), (code, obj))
d.scan(now=now)
with open(E[2], "ab") as fh: fh.write(b"grown since the scan")
code, obj = d.ui_fix(c, E[1]); check("pack: when a later file cannot be moved, the files already moved go back and the torrent stays in the client", code == 409 and os.lstat(E[1]).st_ino == inos[1] and len(world["_removed"]) == n_rm and not any(h["orig"] in E.values() for h in d.holding(c)["items"]), (code, obj))
with open(E[2], "r+b") as fh: fh.truncate(os.path.getsize(E[2]) - len(b"grown since the scan"))
d.scan(now=now); c.execute("INSERT INTO ignored(path, at) VALUES(?, ?)", (E[2], now)); c.commit(); code, obj = d.ui_fix(c, E[1])
check("pack: a file of the torrent you chose to ignore keeps the whole torrent", code == 409 and "ignore" in obj["error"] and os.path.exists(E[1]) and not row(E[1])["fixable"], (code, obj)); c.execute("DELETE FROM ignored WHERE path=?", (E[2],)); c.commit()
code, obj = d.ui_fix(c, E[2] if False else E[1]); held = [h for h in d.holding(c)["items"] if h["orig"] in E.values()]
check("pack: one press moves both spare copies to the holding bin, leaves the hardlink, and the client forgets the torrent once, files kept", code == 200 and obj["files"] == 2 and not os.path.exists(E[1]) and not os.path.exists(E[2]) and os.lstat(E[3]).st_ino == inos[3] and world["_removed"][n_rm:] == [("pack1", False)] and len(held) == 2, (code, obj, world["_removed"][n_rm:]))
check("pack: each held file says its torrent is gone, and the note counts files and space", all("torrent" in h["note"] for h in held) and "its 2 files" in obj["note"] and "1 that is linked" in obj["note"], obj.get("note"))
code, obj = d.restore_held(c, held[0]["id"]); check("pack: Put back returns one file of the pack on its own", code == 200 and os.path.exists(held[0]["orig"]), (code, obj))
d.FIX_MODE = "dryrun"
# ---- P3.5 (2026-11-15): the lock shared with Auditarr. One marker per file in LOCK_DIR, named after the inode, holding {"app","what","path","since"};
# the same shape is checked in Auditarr's tests/lock.py. While Auditarr holds a file, no fix here touches it
def theirs(path, app="Auditarr", what="rewrapping the file", age=0):
    os.makedirs(d.LOCK_DIR, exist_ok=True); m = os.path.join(d.LOCK_DIR, f"{os.stat(path).st_ino}.lock")
    json.dump(dict(app=app, what=what, path=path, since=time.time() - age), open(m, "w")); os.utime(m, (time.time() - age, time.time() - age)); return m
d.FIX_MODE = "on"; LK = os.path.join(DATA, "torrents", "tv", "Lock.Show.S01E01.1080p.WEB-GRP.mkv"); LL = os.path.join(DATA, "media", "tv", "Lock Show", "Season 01", "Lock Show - S01E01.mkv"); LO = os.path.join(DATA, "torrents", "tv", "Lock.Other.S01E01.1080p.WEB-GRP.mkv")
fake_disk.blob(LK, 3, "lock-e1"); fake_disk.blob(LL, 3, "lock-e1"); fake_disk.blob(LO, 2, "lock-other"); d.scan(now=now); inos = {p: os.lstat(p).st_ino for p in (LK, LL, LO)}
check("lock: the lock folder sits at the top of the first scanned folder, next to the holding bin", d.LOCK_DIR == os.path.join(DATA, ".fix-locks") and os.path.dirname(d.LOCK_DIR) == os.path.dirname(d.HOLDING_DIR), d.LOCK_DIR)
m = theirs(LO); code, obj = d.hold_file(c, LO, "bin", "test")
check("lock: a file Auditarr is rewriting is not moved, and the answer says who has it and what they are doing", code == 409 and obj["error"].startswith("Auditarr is working on this file right now (rewrapping the file)") and "nothing was changed" in obj["error"] and os.lstat(LO).st_ino == inos[LO] and os.path.exists(m), (code, obj))
code, obj = d.ui_fix_all(c, [LO]); check("lock: 'Move all' leaves that file alone and names the reason", code == 200 and obj["done"] == 0 and os.path.exists(LO), (code, obj))
os.remove(m); m = theirs(LL); v = {"path": LK, "twin": LL, "code": "DUPLICATE_COPY", "client": None}; code, obj = d.relink_fix(c, v, ["x"])
check("lock: a relink waits when Auditarr holds the library file, both files untouched and no marker of ours left behind", code == 409 and "Auditarr is working on this file" in obj["error"] and os.lstat(LK).st_ino == inos[LK] and os.lstat(LL).st_nlink == 1 and os.listdir(d.LOCK_DIR) == [os.path.basename(m)] and not d._LOCKS, (code, obj, os.listdir(d.LOCK_DIR)))
os.utime(m, (time.time() - 7 * 3600,) * 2); seen = []; real_same = d.same_bytes
def spying(a, b, tick=None): seen.extend(sorted((json.load(open(os.path.join(d.LOCK_DIR, n)))["app"], json.load(open(os.path.join(d.LOCK_DIR, n)))["path"]) for n in os.listdir(d.LOCK_DIR))); return real_same(a, b, tick)
d.same_bytes = spying; code, obj = d.relink_fix(c, v, ["x"]); d.same_bytes = real_same
check("lock: a marker older than six hours is taken over, and during the comparison both files carry this app's marker", code == 200 and seen == sorted([("Dedupearr", LK), ("Dedupearr", LL)]) and os.lstat(LK).st_ino == inos[LL], (code, obj, seen))
check("lock: when the fix is done every marker is gone", os.listdir(d.LOCK_DIR) == [] and not d._LOCKS, os.listdir(d.LOCK_DIR))
hid = next(h["id"] for h in d.holding(c)["items"] if h["orig"] == LK); m = theirs(LL); code, obj = d.restore_held(c, hid)
check("lock: Put back after a relink waits while Auditarr holds the library file the old name points at", code == 409 and "Auditarr is working on this file" in obj["error"] and os.lstat(LK).st_ino == inos[LL], (code, obj))
os.remove(m); m = theirs(LO, app="Dedupearr"); code, obj = d.hold_file(c, LO, "bin", "test")
check("lock: a marker this app left behind before a restart does not block it", code == 200 and not os.path.exists(LO) and os.listdir(d.LOCK_DIR) == [], (code, obj))
a = theirs(LL, app="Dedupearr"); n = d.lock_sweep(); check("lock: start-up clears this app's old markers", n == 1 and not os.path.exists(a))
real_dir = d.LOCK_DIR; d.LOCK_DIR = os.path.join(TMP, "nowhere", "deeper", ".fix-locks"); code, obj = d.restore_held(c, hid); d.LOCK_DIR = real_dir
check("lock: with no place for the lock folder a fix runs as before and nothing is created", code == 200 and os.lstat(LK).st_ino == inos[LK] and not os.path.exists(os.path.join(TMP, "nowhere")), (code, obj))
d.scan(now=now); check("lock: the scan never lists the lock folder", not any(".fix-locks" in i["path"] for i in d.state(c)["items"]))
# ---- CodeQL #13 (2026-11-16): the lock reads the disk (the file's number), so it must never be handed a path straight from a request.
# hold_file checks the path against the scanned folders and the scan's own record first; only then does the lock look at the file.
looked = []; real_marker = d._lock_marker; d._lock_marker = lambda p: (looked.append(p), real_marker(p))[1]
STRAY = os.path.join(DATA, "movies", "never-scanned.mkv"); fake_disk.blob(STRAY, 2, "never-scanned")
code1, obj1 = d.hold_file(c, "/etc/hostname", "bin", "test"); code2, obj2 = d.hold_file(c, DATA + "/../../etc/hostname", "bin", "test"); code3, obj3 = d.hold_file(c, STRAY, "bin", "test")
d._lock_marker = real_marker
check("lock: a path outside the scanned folders is refused before the lock looks at the disk", code1 == 400 and code2 == 400 and "outside the folders" in obj1["error"], (code1, code2, obj1))
check("lock: a file the scan never recorded is refused before the lock looks at the disk, and stays where it is", code3 == 409 and "no record of that file" in obj3["error"] and os.path.exists(STRAY), (code3, obj3))
check("lock: none of those refused paths ever reached the lock", looked == [], looked)
os.remove(STRAY)
d.FIX_MODE = "dryrun"
srv.shutdown(); srv.server_close(); c.close(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
