"""Living next to Auditarr (P2.4): Dedupearr reads Auditarr's list of files it changed (/api/changed) and where its trash is.
A pretend Auditarr serves the list; the fake disk gains an Auditarr trash folder and a library file Auditarr rewrote.
Run: python tests/auditarr_link.py   Exit 1 on failure."""
import os, sys, json, time, tempfile, threading, atexit, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-aud-"); DATA = os.path.join(TMP, "data")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)   # the fake disk goes when the test ends, pass or fail (#36)
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100", SONARR_URL="http://127.0.0.1:9", SONARR_APIKEY="k", CLIENT_TIMEOUT="3")
for k in ("AUDITARR_URL", "AUDITARR_PATH_MAP"): os.environ.pop(k, None)
import fake_disk  # noqa: E402
answer = {"code": 200, "body": b"{}"}
class A(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(answer["code"]); self.end_headers(); self.wfile.write(answer["body"] if self.path == "/api/changed" else b"{}")
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), A); threading.Thread(target=srv.serve_forever, daemon=True).start()
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:300]}]"))

now = time.time(); P = fake_disk.build(DATA, now); blob = fake_disk.blob
TR = f"{DATA}/media/.media-audit-trash"   # Auditarr's default name, inside a folder called "media": by its parents alone it would look like library
P["tr_plain"] = blob(f"{TR}/regrab/2026-11-10/media/tv/Old Show/Old.Show.S01E01.mkv", 2, "old-show", now - 400 * 86400)   # an old file: a plain recycle rule would call it overdue
P["tr_twin"] = blob(f"{TR}/dedupe/2026-11-12/media/movies/Twice (2020)/Twice.2020.1080p.mkv", 7, "twice")                 # same bytes as a download below
P["tr_twin_dl"] = blob(f"{DATA}/torrents/movies/Twice.2020.1080p.mkv", 7, "twice")
P["tr_link"] = blob(f"{TR}/regrab/2026-11-13/media/tv/Linked/Linked.S01E01.mkv", 2, "linked")                             # the torrent name is a hardlink of a trashed file
P["tr_link_dl"] = f"{DATA}/torrents/tv/Linked.S01E01.mkv"; os.makedirs(os.path.dirname(P["tr_link_dl"]), exist_ok=True); os.link(P["tr_link"], P["tr_link_dl"])
CUSTOM = f"{DATA}/media/removed-by-audit"   # a trash folder with a name of the owner's choosing: only Auditarr's answer says what it is
P["tr_custom"] = blob(f"{CUSTOM}/regrab/2026-11-14/media/tv/Custom/Custom.S01E01.mkv", 2, "custom")
P["rw_lib"] = blob(f"{DATA}/media/tv/Bling Empire (2021)/Season 01/Bling.Empire.S01E08.mkv", 3, "bling-rewrapped")       # Auditarr rewrapped it: new bytes, new size
P["rw_dl"] = blob(f"{DATA}/torrents/tv/Bling.Empire.S01E08.1080p.WEB.mkv", 4, "bling-original")
d.read_arr_imports = lambda: {P["rw_dl"]: {"app": "sonarr", "imported": P["rw_lib"], "when": "2026-11-01"}}
d.read_arr_queue = lambda: {}; d.read_arr_roots = lambda: {}
def scan():
    c = d.db(); d.scan(c, now=time.time()); s = d.state(c); c.close(); return s, {i["path"]: i for i in s["items"]}

# ---- without an address: Auditarr's trash is still known by its folder name
S, by = scan(); code = lambda k: by[P[k]]["code"]
check("no address: a file in Auditarr's trash (by its folder name) is 'In Auditarr's trash', not library, not an overdue bin file", code("tr_plain") == "AUDITARR_BIN" and by[P["tr_plain"]]["kind"] == "recycle" and by[P["tr_plain"]]["wasted"] == 0, by[P["tr_plain"]])
check("Dedupearr never offers to move or empty it", not any(by[P[k]]["fixable"] or by[P[k]]["needs_you"] for k in ("tr_plain", "tr_twin", "tr_link")))
check("its sentence says Auditarr looks after it", "Auditarr deletes it for good" in by[P["tr_plain"]]["plain"] and "leaves it alone" in by[P["tr_plain"]]["plain"], by[P["tr_plain"]]["plain"])
check("a download with the same bytes as a trashed file is not 'stored twice' with the trash, and gets no relink", code("tr_twin_dl") != "DUPLICATE_COPY" and by[P["tr_twin_dl"]]["twin"] is None and by[P["tr_twin_dl"]]["fix_kind"] != "relink", by[P["tr_twin_dl"]])
check("a torrent name hardlinked to a trashed file is no longer a 'Seeding name' of a library file (seen on the real library 2026-11-16)", code("tr_link_dl") != "LINKED_SEED" and "Auditarr's trash" in by[P["tr_link_dl"]]["evidence"], by[P["tr_link_dl"]])
check("the trash does not count as library size", S["library_bytes"] == sum(i["size"] for i in S["items"] if i["kind"] == "library" and i["code"] != "LIBRARY_TWIN") + sum(i["size"] for i in S["items"] if i["code"] == "LIBRARY_TWIN") // 2, S["library_h"])
check("without an address a header-differs copy keeps the careful guess", code("hdr_dl") == "DUPLICATE_HEADER" and by[P["hdr_dl"]]["auditarr"] is None and "usually means" in by[P["hdr_dl"]]["plain"])
check("without an address a trash folder with another name is not recognised (it needs Auditarr's answer)", code("tr_custom") != "AUDITARR_BIN")
check("About says nothing about Auditarr when no address is set", (c_ := d.db()) and not any("Auditarr" in h["text"] and "could not" in h["text"] for h in d.about(c_)["health"]) and d.state(c_)["auditarr"] == {})

# ---- with an address: the list explains the changed files, and names the trash folder
t_def = now - 3 * 86400; day = time.strftime("%Y-%m-%d", time.localtime(t_def))
answer["body"] = json.dumps({"app": "Auditarr", "trash_dir": CUSTOM, "trash_days": 14, "files": [[P["hdr_lib"], t_def, "set the default audio and subtitle tracks"], [P["rw_lib"], t_def, "rewrapped the file"], ["/elsewhere/x.mkv", 1, "labelled the audio"], "junk", [1, 2, 3]]}).encode()
d.AUDITARR_URL = f"http://127.0.0.1:{srv.server_address[1]}"
S, by = scan(); h = by[P["hdr_dl"]]; rw = by[P["rw_dl"]]
check("a header-differs copy now says Auditarr changed the library file, what and when", h["code"] == "DUPLICATE_HEADER" and h["auditarr"] == {"what": "set the default audio and subtitle tracks", "when": day} and f"because Auditarr changed the library copy (it set the default audio and subtitle tracks on {day})" in h["plain"], h["plain"])
check("its evidence carries the same fact", f"Auditarr changed the library copy on {day}" in h["evidence"], h["evidence"])
check("it says the torrent is still needed for seeding and never offers a relink", "only still needed for seeding" in h["plain"] and h["fix_kind"] in ("", "remove") and d.plan_fix(h)[0] != "relink", (h["fix_kind"], h["plain"]))
check("a download whose library file Auditarr rewrote is a leftover that names Auditarr", rw["code"] == "LEFTOVER" and rw["twin"] == P["rw_lib"] and "Auditarr has since rewritten the library file" in rw["plain"] and f"it rewrapped the file on {day}" in rw["plain"] and "only still needed for seeding" in rw["plain"], rw["plain"])
check("and never a relink either", rw["fix_kind"] != "relink" and d.plan_fix(rw)[0] != "relink")
check("the trash folder Auditarr names is recognised whatever it is called", by[P["tr_custom"]]["code"] == "AUDITARR_BIN" and by[P["tr_custom"]]["kind"] == "recycle")
check("junk rows in Auditarr's answer are skipped, good ones kept", d.AUDITARR_STATUS.get("ok") is True and d.AUDITARR_STATUS.get("n") == 3, d.AUDITARR_STATUS)
c_ = d.db(); ab = d.about(c_)
check("About says Auditarr was read and its trash is left alone", any(x["level"] == "ok" and "Auditarr: 3 files it changed are known; its trash is left alone." == x["text"] for x in ab["health"]), [x["text"] for x in ab["health"]])
check("About lists the connection", ab["settings"]["connections"].get("Auditarr") == d.AUDITARR_URL)
check("no sentence leaks a path or braces", all(DATA not in i["plain"] and "{" not in i["plain"] for i in S["items"]))
su = {x["name"]: x for x in d.setup_view(c_)["connections"]}["Auditarr"]
check("the Setup tab shows what Auditarr said on the last scan (#37)", su["status"] == "ok" and "3 files" in su["note"], "the Auditarr row on Setup had no status or note from the last scan")

# ---- paths mapped when Auditarr sees the files elsewhere
d.AUDITARR_PATH_MAP = [("/mnt/lib", DATA)]
answer["body"] = json.dumps({"trash_dir": "/mnt/lib/media/removed-by-audit", "files": [["/mnt/lib" + P["hdr_lib"][len(DATA):], t_def, "labelled the audio"]]}).encode()
S, by = scan()
check("AUDITARR_PATH_MAP translates Auditarr's paths to ours", by[P["hdr_dl"]]["auditarr"] == {"what": "labelled the audio", "when": day} and by[P["tr_custom"]]["code"] == "AUDITARR_BIN", by[P["hdr_dl"]]["auditarr"])
d.AUDITARR_PATH_MAP = []
# ---- a path map works on whole folder names (#34)
d.AUDITARR_PATH_MAP = [("/mnt/lib", "/ours")]; d.CLIENT_PATH_MAP = [("/downloads/", "/ours/torrents")]
check("a path map rewrites its own folder", d.map_auditarr_path("/mnt/lib/a.mkv") == "/ours/a.mkv" and d.map_client_path("/downloads/tv/a.mkv") == "/ours/torrents/tv/a.mkv" and d.map_client_path("/downloads") == "/ours/torrents", "a mapped folder was not rewritten")
check("a path map never rewrites a folder that only starts with the same letters", d.map_auditarr_path("/mnt/library2/a.mkv") == "/mnt/library2/a.mkv" and d.map_client_path("/downloads2/a.mkv") == "/downloads2/a.mkv", "a look-alike folder was rewritten")
d.AUDITARR_PATH_MAP = []; d.CLIENT_PATH_MAP = []

# ---- an Auditarr that does not answer: nothing breaks, the guess returns, About and the morning message say so
for bad in ((500, b"oops"), (200, b"<html>sign in</html>"), (200, b'{"files": "x"}')):
    answer["code"], answer["body"] = bad; S, by = scan()
    check(f"Auditarr answering {bad[0]} {bad[1][:10]!r}: the scan still finishes and the careful guess returns", S["last_run"]["note"] == "ok" and by[P["hdr_dl"]]["code"] == "DUPLICATE_HEADER" and by[P["hdr_dl"]]["auditarr"] is None and "usually means" in by[P["hdr_dl"]]["plain"], S["last_run"])
check("its trash is still recognised by name", by[P["tr_plain"]]["code"] == "AUDITARR_BIN")
ab = d.about(c_); dg = d.digest(c_)
check("About warns that Auditarr could not be read", any(x["level"] == "warn" and x["text"].startswith("Auditarr could not be read") for x in ab["health"]), [x["text"] for x in ab["health"]])
check("the morning message names it as trouble", "Auditarr could not be read" in dg["lines"][-1], dg["lines"][-1])
c_.close(); srv.shutdown(); bad_n = results.count(False); print(f"\n{len(results) - bad_n} of {len(results)} checks passed"); sys.exit(1 if bad_n else 0)
