"""P2.5 (2026-11-22): the Plex reader. With the fake Plex answering (tests/mock_clients.py), a library file in no Plex library folder is
"Outside Plex's folders", one inside a Plex folder that Plex has not indexed is "Plex does not show it" after a day of grace, and nothing is
said about Plex at all unless the whole of Plex was read: a Plex that is down, refuses the token, or loses one page of one library gives no
Plex verdicts that night. The token never reaches a log line, an address or the page. Run: python tests/plex.py   Exit 1 on failure."""
import os, sys, json, time, tempfile, socket, atexit, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-plex-"); DATA = os.path.join(TMP, "data")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)   # the fake disk goes when the test ends, pass or fail (#36)
import fake_disk, mock_clients  # noqa: E402
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); M = f"http://127.0.0.1:{port}"
now = time.time(); P = fake_disk.build(DATA, now); P.update(mock_clients.extra_files(DATA, fake_disk.blob)); world = mock_clients.world_for(P, DATA)
srv = mock_clients.start(world, port); SECRET = world["plex"]["token"]
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100", CLIENT_TIMEOUT="5", **mock_clients.env_for(M))
os.environ.pop("PLEX_PATH_MAP", None)
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:300]}]"))
c = d.db(); tick = [0]
def rescan(at=None):
    """One more scan a few seconds later (or at a given moment); -> rows by path."""
    tick[0] += 5; d.scan(now=at or now + tick[0]); return {i["path"]: i for i in d.state(c)["items"]}
plexed = lambda rows: sorted(os.path.basename(p) for p, i in rows.items() if i["code"] in d.PLEX_CODES)
words = lambda: json.dumps([d.LOG, d.ERRORS, d.actions(c), d.settings_view(), d.state(c), d.about(c), d.setup_view(c), d.digest(c)], default=str)

by = rescan(); S = d.state(c); code = lambda k, rows=None: (rows or by).get(P[k], {}).get("code"); ev = lambda k, rows=None: (rows or by)[P[k]]["evidence"]
check("the fake Plex was read in full: two film and show libraries, their files, and the music library named as skipped",
      d.PLEX_STATUS.get("ok") and d.PLEX_STATUS["libraries"] == 2 and d.PLEX_STATUS["n"] == sum(len(x["media"][0]) for sec in world["plex"]["sections"] for x in sec["items"]) and "skipped (Music)" in d.PLEX_STATUS["note"], d.PLEX_STATUS)
check("a music library is never asked for its files", world.get("_plex_asked") == ["1", "2"], world.get("_plex_asked"))
# the two verdicts
o = by[P["px_outside"]]
check("a library file in a folder no Plex library looks in = Outside Plex's folders: not waste, needs you, no fix button, Ignore offered",
      o["code"] == "NOT_IN_PLEX_FOLDER" and o["label"] == "Outside Plex's folders" and o["wasted"] == 0 and o["needs_you"] and not o["fixable"] and not o["fix_kind"] and o["kind"] == "library", (o["code"], o["wasted"], o["needs_you"], o["fixable"]))
check("its evidence names Plex's libraries and their folders, and the path-map setting for the case Plex sees another path",
      f"Films at {DATA}/media/movies" in o["evidence"] and f"Shows at {DATA}/media/tv" in o["evidence"] and "PLEX_PATH_MAP" in o["evidence"], o["evidence"])
check("its sentence says what would change it, in plain words", "Add this folder to a Plex library, or move the file into a folder Plex already has" in o["plain"] and o["plain"].endswith("."), o["plain"])
u = by[P["px_unindexed"]]
check("a three-day-old library file inside Plex's film folder that Plex has not indexed = Plex does not show it: not waste, a quiet note (does not need you), no button",
      u["code"] == "NOT_IN_PLEX" and u["label"] == "Plex does not show it" and u["wasted"] == 0 and not u["needs_you"] and not u["fixable"], (u["code"], u["wasted"], u["needs_you"]))
check("its evidence names the Plex library and the folder", f"inside {DATA}/media/movies, a folder of Plex's library Films" in u["evidence"], u["evidence"])
check("its sentence says the usual reasons and what to do", "cannot match the name" in u["plain"] and "run a library scan in Plex" in u["plain"], u["plain"])
t = by[P["px_trailer"]]
check("a trailer in a film's Trailers folder = Plex does not show it, and the sentence says that is expected for an extra", t["code"] == "NOT_IN_PLEX" and "which Plex does not list on its own, so that is expected" in t["plain"], t["plain"])
check("neither Plex verdict offers a fix: the plan says there is nothing to fix", all(d.plan_fix(dict(by[P[k]]))[0] is None for k in ("px_outside", "px_unindexed", "px_trailer")))
check("neither counts as space to get back or to decide", all(S["wasted_by"].get(k, 0) == 0 for k in d.PLEX_CODES) and not any(i["wasted"] for i in S["items"] if i["code"] in d.PLEX_CODES))
# the day of grace
f = by[P["px_fresh"]]
check("an hour-old library file Plex has not indexed yet is never called unindexed: it stays Only copy, and the evidence says why",
      f["code"] == "LIBRARY_ONLY" and "Plex has not indexed it yet" in f["evidence"] and "under 24 hours old" in f["evidence"], (f["code"], f["evidence"]))
check("22 hours later it is still within the day of grace", code("px_fresh", rescan(now + 22 * 3600)) == "LIBRARY_ONLY")
late = rescan(now + 25 * 3600)
check("25 hours later, still not indexed, it is Plex does not show it", code("px_fresh", late) == "NOT_IN_PLEX", code("px_fresh", late))
by = rescan()
# what stays as it was
check("a concert film inside Plex's music library is left alone (Only copy): its folder is Plex's, its files are not read",
      code("px_music") == "LIBRARY_ONLY" and "not a film or show library" in ev("px_music") and "Music" in ev("px_music"), ev("px_music"))
check("library files Plex has indexed keep their verdict and their evidence says nothing about Plex", code("lib_only") == code("dup_lib") == "LIBRARY_ONLY" and code("real_lib") == "REAL_COPY" and not any("Plex" in ev(k) for k in ("lib_only", "dup_lib", "real_lib", "hdr_lib")), ev("lib_only"))
check("the second name of a library twin is not called unindexed when Plex shows the file under its other name", code("twin_b") == "LIBRARY_TWIN" and "Plex" not in ev("twin_b"), ev("twin_b"))
check("downloads, recycle bins and unsorted folders are never judged by Plex", all(i["kind"] == "library" for i in by.values() if i["code"] in d.PLEX_CODES) and code("bin_old") == "BIN_OLD" and code("bin_new") == "BIN_RECENT" and code("other") == "OTHER" and "Plex" not in ev("bin_old") + ev("other") + ev("dup_dl"))
check("exactly the planted files carry a Plex verdict", plexed(by) == sorted(os.path.basename(P[k]) for k in ("px_outside", "px_unindexed", "px_trailer")), plexed(by))
base = {p: (i["code"], i["evidence"]) for p, i in by.items()}
check("a second scan of the same disk and the same Plex changes nothing", {p: (i["code"], i["evidence"]) for p, i in rescan().items()} == base)
# a file that is already something more specific keeps its verdict; the Plex fact joins its evidence
for k in ("real_lib", "twin_a"): os.utime(P[k], (now - 5 * 86400,) * 2)   # older than the day of grace
films = world["plex"]["sections"][0]; keep = list(films["items"]); films["items"] = [x for x in keep if x["media"][0][0] not in (P["real_lib"], P["twin_a"])]
m = rescan()
check("a real copy Plex has not indexed stays Real copy, with the Plex fact in its evidence", code("real_lib", m) == "REAL_COPY" and "but Plex has not indexed this file" in ev("real_lib", m) and "inode" in ev("real_lib", m), ev("real_lib", m))
check("a library twin Plex shows under neither name stays Two names, one file, with the Plex fact in its evidence", code("twin_a", m) == code("twin_b", m) == "LIBRARY_TWIN" and all("Plex has not indexed" in ev(k, m) for k in ("twin_a", "twin_b")), ev("twin_a", m))
films["items"] = keep
# several versions and several parts of one item are all indexed files
films["items"] = keep + [{"title": "Two versions, one in two parts", "media": [[P["px_unindexed"]], [P["px_trailer"], P["px_outside"]]]}]
m = rescan()
check("every version and every part of an item counts as indexed", code("px_unindexed", m) == code("px_trailer", m) == "LIBRARY_ONLY", (code("px_unindexed", m), code("px_trailer", m)))
check("a file Plex shows from outside its own folders is not called outside them", code("px_outside", m) == "LIBRARY_ONLY", code("px_outside", m))
films["items"] = keep
# paging
calls = world["_plex_calls"]; rescan(); one = world["_plex_calls"] - calls; d.PLEX_PAGE = 2; calls = world["_plex_calls"]; m = rescan(); paged = world["_plex_calls"] - calls
check("a library longer than one page is read page by page, with the same answer", paged > one and {p: (i["code"], i["evidence"]) for p, i in m.items()} == base and d.PLEX_STATUS["ok"], (one, paged, d.PLEX_STATUS))
for how, what in (("error", "an error on the second page"), ("short", "a second page that comes back empty")):
    world["plex"]["fail"] = {"key": "1", "start": 2, "how": how}; m = rescan(); a = d.about(c)
    check(f"{what} of one library: no Plex verdict at all that night, not even from the library that was read in full", plexed(m) == [] and not d.PLEX_STATUS["ok"] and code("px_outside", m) == code("px_unindexed", m) == "LIBRARY_ONLY", (plexed(m), d.PLEX_STATUS))
    check(f"{what}: the scan still finishes, and About says Plex could not be read", d.state(c)["last_run"]["note"] == "ok" and any(h["level"] == "warn" and h["text"].startswith("Plex could not be read: ") and h["text"].endswith(".") for h in a["health"]), [h["text"][:90] for h in a["health"]])
world["plex"].pop("fail"); d.PLEX_MAX_PAGES = 1; m = rescan()
check("a library that does not end within the page limit counts as not read: no Plex verdicts", plexed(m) == [] and not d.PLEX_STATUS["ok"] and "not read to the end" in d.PLEX_STATUS["note"], d.PLEX_STATUS)
d.PLEX_MAX_PAGES = 500; d.PLEX_PAGE = 1000
check("with Plex whole again the verdicts are back", {p: (i["code"], i["evidence"]) for p, i in rescan().items()} == base)
# the order Plex lists its libraries in, with folders that overlap
extra = [{"key": "7", "type": "movie", "title": "Classics", "locations": [f"{DATA}/media/movies/Heat (1995)"], "items": []}, {"key": "8", "type": "movie", "title": "Archive", "locations": [f"{DATA}/media/movies"], "items": []}]
secs = world["plex"]["sections"]; world["plex"]["sections"] = secs + extra; fwd = {p: (i["code"], i["evidence"]) for p, i in rescan().items()}
world["plex"]["sections"] = list(reversed(secs + extra)); rev = {p: (i["code"], i["evidence"]) for p, i in rescan().items()}
check("the answer does not depend on the order Plex lists its libraries in, even when their folders overlap", fwd == rev, [p for p in fwd if fwd[p] != rev.get(p)][:2])
check("a file under two libraries' folders is named with the deepest folder", "Heat (1995), a folder of Plex's library Classics" in fwd[P["px_trailer"]][1], fwd[P["px_trailer"]][1])
world["plex"]["sections"] = secs
# a Plex that sees the files under another path
world["plex"]["prefix"] = ("/plexdata", DATA); m = rescan()
check("Plex under another path and no path map: no folder in common, so nothing is compared and About says which setting to use", plexed(m) == [] and not d.PLEX_STATUS["ok"] and "PLEX_PATH_MAP" in d.PLEX_STATUS["note"] and "/plexdata/media" in d.PLEX_STATUS["note"], d.PLEX_STATUS)
d.PLEX_PATH_MAP = [("/plexdata", DATA)]; m = rescan()
check("with the path map set, a path-mapped Plex gives the same verdicts", {p: i["code"] for p, i in m.items()} == {p: v[0] for p, v in base.items()} and d.PLEX_STATUS["ok"], (plexed(m), d.PLEX_STATUS))
d.PLEX_PATH_MAP = [("/plex", "/ours"), ("C:\\Media", "/data/media")]
check("the path map works on whole folder names and on a Windows Plex's paths", [d.map_plex_path(x) for x in ("/plex/a.mkv", "/plex2/a.mkv", "/plex", "C:\\Media\\Films\\A (2020)\\a.mkv")] == ["/ours/a.mkv", "/plex2/a.mkv", "/ours", "/data/media/Films/A (2020)/a.mkv"], [d.map_plex_path(x) for x in ("/plex/a.mkv", "/plex2/a.mkv", "/plex", "C:\\Media\\Films\\A (2020)\\a.mkv")])
d.PLEX_PATH_MAP = []; world["plex"].pop("prefix")
# a Plex with nothing to compare
world["plex"]["sections"] = [secs[2]]; m = rescan()
check("a Plex with only a music library: nothing is compared, no verdicts, and the note says so without calling it broken", plexed(m) == [] and d.PLEX_STATUS["ok"] and "no film or show library" in d.PLEX_STATUS["note"], d.PLEX_STATUS)
world["plex"]["sections"] = secs
# a refused token, an answer that is not Plex's, a Plex that is down
good = d.PLEX_TOKEN; d.PLEX_TOKEN = "not-the-right-one"; m = rescan(); a = d.about(c)
check("a refused token (401): the scan finishes, no Plex verdicts, and the note says in plain words that the token was refused", d.state(c)["last_run"]["note"] == "ok" and plexed(m) == [] and d.PLEX_STATUS == {"ok": False, "n": 0, "libraries": 0, "note": "it refused the token (set the Plex token again)"}, "the status after a 401 was not the plain note")
check("Test on the Setup tab names a refused token too", d.test_connection("Plex") == (False, "Plex answered with an error (401: the key, token or password was refused)."), "the Test answer for a 401 was not the plain sentence")
check("the morning message lists Plex under trouble", "Plex could not be read" in d.digest(c)["trouble"], d.digest(c)["trouble"])
d.PLEX_TOKEN = good
check("Test on the Setup tab says how many libraries Plex has, by name", d.test_connection("Plex") == (True, "Plex answered: 3 libraries (Films, Music, Shows)."), "the Test answer was not the plain sentence")
try: d.http_json(M + "/plex/library/sections", headers={"X-Plex-Token": good}); said = "no error"
except Exception as e: said = d.plex_error(e)
check("an answer that is not JSON (Plex without the JSON header, or a sign-in page) is named in words", said == "something answered at its address, but not Plex (another app, or a sign-in page)", said)
d.PLEX_URL = M + "/sonarr"; m = rescan()
check("another app at Plex's address: no verdicts, a plain note", plexed(m) == [] and not d.PLEX_STATUS["ok"] and d.PLEX_STATUS["note"] == "it answered with an error (404)", d.PLEX_STATUS)
d.PLEX_URL = "http://127.0.0.1:9"; errs = len(d.ERRORS); m = rescan(); a = d.about(c)
check("Plex down: the scan finishes and no file carries a Plex verdict", d.state(c)["last_run"]["note"] == "ok" and plexed(m) == [] and code("px_outside", m) == "LIBRARY_ONLY", plexed(m))
check("Plex down: About says so and the error is kept for Recent errors", not d.PLEX_STATUS["ok"] and any(h["text"].startswith("Plex could not be read") for h in a["health"]) and any(e["text"].startswith("Plex: ") for e in d.ERRORS[errs:]), d.PLEX_STATUS)
check("the page's state carries Plex's status for the page", d.state(c)["plex"].get("ok") is False and "Plex" in {k["name"] for k in d.setup_view(c)["connections"]})
# the token
d.PLEX_URL = M + "/plex"; by = rescan(); d.PLEX_STATUS.clear()
check("after a restart Plex's status is remembered from the last scan", d.plex_status().get("ok") is True and d.plex_status()["libraries"] == 2)
check("the token was only ever sent in a header, never in an address", "_plex_token_in_url" not in world and world["_plex_calls"] > 20, "a request carried the token in its address")
check("Dedupearr only reads Plex: no write call reached it", not any(w[0] == "Plex" for w in world.get("_writes", [])), "a write call reached the fake Plex")
check("the token is in no log line, no error, no action, and in nothing the page is sent (settings, setup, state, About, the morning message)", SECRET not in words() and "not-the-right-one" not in words(), "the token was found in an output")
check("the settings answer says a token is saved, never what it is", next(i for i in d.settings_view()["items"] if i["name"] == "PLEX_TOKEN") | {"hint": ""} == {"name": "PLEX_TOKEN", "group": "conn:Plex", "kind": "secret", "label": "Token", "hint": "", "default": "", "source": "container", "locked": True, "value": "", "saved": True}, "the token's settings row was not the masked, locked one")
check("About lists Plex like the other services", any(h["level"] == "ok" and h["text"] == f"Plex: 2 libraries, {d.PLEX_STATUS['n']:,} files indexed; 1 music, photo or other library skipped (Music)." for h in d.about(c)["health"]) and d.about(c)["settings"]["connections"]["Plex"] == M + "/plex", [h["text"] for h in d.about(c)["health"] if "Plex" in h["text"]])
# no Plex at all: nothing anywhere
d.PLEX_URL = ""; errs = len(d.ERRORS); m = rescan(); S0 = d.state(c); a = d.about(c)
check("with no Plex address there is no Plex verdict, no Plex evidence, no status, no error, no About line and no extra kind of verdict on the Space tab",
      plexed(m) == [] and not any("Plex" in i["evidence"] for i in m.values()) and d.PLEX_STATUS == {} and S0["plex"] == {} and len(d.ERRORS) == errs and not any(h["text"].startswith("Plex") for h in a["health"]) and not set(d.PLEX_CODES) & set(S0["labels"]) and "Plex could not be read" not in d.digest(c)["trouble"],
      (plexed(m), d.PLEX_STATUS, [h["text"] for h in a["health"] if "Plex" in h["text"]]))
check("without an address, Test says what is missing", d.test_connection("Plex") == (False, "Plex has no address yet. Type it above and press Save first."))
srv.shutdown(); srv.server_close(); c.close(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
