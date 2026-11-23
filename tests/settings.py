"""P1.7 and P4.1 (2026-11-18): the Settings and Setup tabs. A value set on the container always wins and cannot be changed from the page; what the
page saves lands in the config folder's database, survives a restart and changes the next scan; passwords and keys never come back to the page;
a new address forgets the old password; without a sign-in only your own network may change anything; the shared-mount test says whether a
library file and a download can be one file, and what to change when not. Run: python tests/settings.py   Exit 1 on failure."""
import os, sys, json, time, tempfile, socket, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-settings-"); DATA = os.path.join(TMP, "data")
import fake_disk, mock_clients  # noqa: E402
s = socket.socket(); s.bind(("127.0.0.1", 0)); mport = s.getsockname()[1]; s.close(); M = f"http://127.0.0.1:{mport}"
now = time.time(); P = fake_disk.build(DATA, now); P.update(mock_clients.extra_files(DATA, fake_disk.blob)); world = mock_clients.world_for(P, DATA); msrv = mock_clients.start(world, mport)
env = mock_clients.env_for(M); env.pop("RADARR_URL"); env.pop("RADARR_APIKEY"); env.pop("PLEX_URL"); PLEX_SECRET = env.pop("PLEX_TOKEN")   # Radarr and Plex are typed on the Setup tab in this test
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100", CLIENT_TIMEOUT="5", **env)
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:240]}]"))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); srv = d.serve(port, block=False); base = f"http://127.0.0.1:{port}"
def call(path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json", **(headers or {})}, method="POST" if body is not None else "GET")
    try: r = urllib.request.urlopen(req, timeout=20); return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
d.load_settings(); d.scan(now=now); c = d.db(); by = lambda: {i["path"]: i for i in d.state(c)["items"]}
st, j = call("/api/settings"); items = {i["name"]: i for i in j.get("items", [])}
check("the Settings answer lists every setting with where it comes from", st == 200 and {"RECYCLE_DAYS", "LIBRARY_HINTS", "SEED_RULE", "SONARR_URL", "DELUGE_PASSWORD"} <= set(items) and items["RECYCLE_DAYS"]["source"] == "default", (st, list(items)[:5]))
check("a value set on the container is shown as set there and locked", items["MIN_SIZE_MB"]["source"] == "container" and items["MIN_SIZE_MB"]["locked"] and items["MIN_SIZE_MB"]["value"] == "1", items["MIN_SIZE_MB"])
check("a password or key never comes back to the page, only whether one is set", items["DELUGE_PASSWORD"]["value"] == "" and items["DELUGE_PASSWORD"]["saved"] is True and "test" not in json.dumps([i for i in j["items"] if i["kind"] == "secret"]), "a secret setting showed its value, or was not marked as saved")
check("a connection whose address is set on the container is locked as a whole (its password too)", items["SONARR_APIKEY"]["locked"] and items["SONARR_URL"]["locked"] and not items["RADARR_URL"]["locked"])
check("this browser on the same machine may change settings", j.get("writable") is True, j.get("why_not"))
check("FIX_MODE, the scanned folders and the holding days are not settings the page can change", not {"FIX_MODE", "SCAN_ROOTS", "HOLD_DAYS", "HOLDING_DIR"} & set(items))
# rules: a saved value changes the next scan, survives a restart, and goes back to the default
check("a 2-day-old bin file is within the 30-day rule", by()[P["bin_new"]]["code"] == "BIN_RECENT")
st, j = call("/api/settings", {"set": {"RECYCLE_DAYS": "1"}})
check("saving a rule answers in words and says to scan to see it", st == 200 and "Scan now" in j.get("note", "") and d.RECYCLE_DAYS == 1, (st, j.get("note"), d.RECYCLE_DAYS))
d.scan(now=now + 1); check("the next scan uses it: the 2-day-old bin file is now past the 1-day rule", by()[P["bin_new"]]["code"] == "BIN_OLD", by()[P["bin_new"]]["code"])
check("it is saved in the database in the config folder", json.loads(d.kv_get(c, "settings"))["RECYCLE_DAYS"] == "1" and d.DB_PATH.startswith(os.environ["CONFIG_DIR"]))
d.SETTINGS.clear(); d.RECYCLE_DAYS = 30; d.load_settings(); check("after a restart the saved value is back", d.RECYCLE_DAYS == 1 and d.setting("RECYCLE_DAYS") == ("1", "page"))
check("the change is in the actions log by name, never by value", any(a["kind"] == "settings" and "RECYCLE_DAYS" in a["result"] for a in d.actions(c)))
st, j = call("/api/settings", {"reset": ["RECYCLE_DAYS"]}); check("Use the default brings the default back", st == 200 and d.RECYCLE_DAYS == 30 and d.setting("RECYCLE_DAYS")[1] == "default", (st, j))
st, j = call("/api/settings", {"set": {"LIBRARY_DIRS": DATA + "/stuff"}}); d.scan(now=now + 2)
check("a folder named as library on the page is judged as library on the next scan (the unsorted folder becomes library)", st == 200 and by()[P["other"]]["kind"] == "library", (st, j.get("error"), by()[P["other"]]["kind"]))
call("/api/settings", {"reset": ["LIBRARY_DIRS"]}); d.scan(now=now + 3)
st, j = call("/api/settings", {"set": {"SEED_RULE": "either", "SEED_MIN_DAYS": "2"}}); check("the seeding rule can be changed from the page", st == 200 and d.SEED_RULE == "either" and d.SEED_MIN_DAYS == 2.0, (st, j))
call("/api/settings", {"reset": ["SEED_RULE", "SEED_MIN_DAYS"]})
for body, why in (({"RECYCLE_DAYS": "ten"}, "whole number"), ({"RECYCLE_DAYS": "0"}, "whole number"), ({"SEED_MIN_RATIO": "-1"}, "between"), ({"SEED_RULE": "sometimes"}, "one of"),
                  ({"SCAN_TIME": "25:00"}, "hours and minutes"), ({"LIBRARY_DIRS": "/etc"}, "not a folder inside"), ({"LIBRARY_DIRS": DATA + "/../x"}, "not a folder inside"),
                  ({"LIBRARY_HINTS": " , "}, "at least one"), ({"RADARR_URL": "ftp://x"}, "http://"), ({"RADARR_URL": "http://a b"}, "http://")):
    st, j = call("/api/settings", {"set": body}); check(f"refused in words: {body}", st == 400 and why in j.get("error", "") and j["error"][0].isupper(), (st, j))
st, j = call("/api/settings", {"set": {"MIN_SIZE_MB": "5"}}); check("a value set on the container is refused by the page and stays", st == 409 and "container" in j["error"] and d.MIN_SIZE_MB == 1, (st, j))
st, j = call("/api/settings", {"set": {"SONARR_APIKEY": "stolen"}}); check("the key of a connection whose address is on the container cannot be changed either", st == 409, (st, j))
st, j = call("/api/settings", {"set": {"FIX_MODE": "on"}}); check("FIX_MODE cannot be switched on from the page", st == 400 and d.FIX_MODE != "on", (st, j))
# connections (P4.1): typed on the Setup tab, tested with what is saved, the password forgotten when the address changes
st, j = call("/api/settings", {"set": {"RADARR_URL": f"{M}/radarr/", "RADARR_APIKEY": "test"}})
check("an arr typed on the Setup tab is used: its address, its key, its source", st == 200 and d.ARRS["radarr"] == {"url": f"{M}/radarr", "key": "test"}, (st, "the saved address or key is not what was typed"))
st, j = call("/api/setup/test", {"name": "Radarr"}); check("Test asks Radarr with what is saved and answers in a sentence", st == 200 and j["ok"] and "Radarr answered" in j["note"] and j["note"].endswith("."), j)
st, j = call("/api/setup/test", {"name": "Deluge"}); check("Test asks a download client (reads only) and says what it knows", j.get("ok") and "4 torrents" in j["note"], j)
cs = dict(d.CLIENT_STATUS); call("/api/setup/test", {"name": "SABnzbd"}); check("a test is not a scan: About keeps what the last scan learned", d.CLIENT_STATUS == cs)
st, j = call("/api/settings", {"set": {"RADARR_URL": "http://127.0.0.1:9/elsewhere"}}); items = {i["name"]: i for i in call("/api/settings")[1]["items"]}
check("a new address forgets the saved key, so a key is never sent to an address it was not typed for", st == 200 and items["RADARR_APIKEY"]["saved"] is False and d.ARRS["radarr"]["key"] == "", "the key was still marked saved, or still in memory, after the address changed")
st, j = call("/api/setup/test", {"name": "Radarr"}); check("Test without a key says what is missing", not j["ok"] and "no address or API key" in j["note"], j)
call("/api/settings", {"set": {"RADARR_URL": f"{M}/radarr", "RADARR_APIKEY": "wrong"}}); st, j = call("/api/setup/test", {"name": "Radarr"})
check("a refused key is named as such", not j["ok"] and "refused" in j["note"], j)
st, j = call("/api/settings", {"set": {"RADARR_APIKEY": ""}}); check("an empty password box keeps the saved one", d.ARRS["radarr"]["key"] == "wrong", "the saved key changed when the box was left empty")
call("/api/settings", {"set": {"RADARR_APIKEY": "test"}})
st, j = call("/api/settings", {"set": {"RTORRENT_URL": "http://127.0.0.1:9/RPC2"}}); check("a client address saved on the page wins over the one an arr lists, and says it came from the page", d.CLIENTS["rTorrent"]["url"].endswith(":9/RPC2") and d.CLIENT_SOURCE["rTorrent"] == "page", d.CLIENT_SOURCE)
call("/api/settings", {"reset": ["RTORRENT_URL"]}); check("Use the default hands it back to the arr's list", "rTorrent" not in d.CLIENT_SOURCE)
# Plex (P2.5): typed on the Setup tab like an arr; its token is a secret like their keys
items = {i["name"]: i for i in call("/api/settings")[1]["items"]}
check("Plex is on the Setup tab with an address and a token, and the token is a password box", items["PLEX_URL"]["group"] == items["PLEX_TOKEN"]["group"] == "conn:Plex" and items["PLEX_TOKEN"]["kind"] == "secret" and items["PLEX_TOKEN"]["label"] == "Token" and not items["PLEX_URL"]["locked"], "the Plex rows of the Settings answer are not an address and a secret")
check("before Plex has an address, no file carries a Plex verdict and Setup shows no Plex status", not any(i["code"] in d.PLEX_CODES for i in by().values()) and next(k for k in call("/api/setup")[1]["connections"] if k["name"] == "Plex")["status"] == "")
st, j = call("/api/settings", {"set": {"PLEX_URL": f"{M}/plex/", "PLEX_TOKEN": PLEX_SECRET}})
check("Plex typed on the Setup tab is used: its address and its token", st == 200 and d.PLEX_URL == f"{M}/plex" and d.PLEX_TOKEN == PLEX_SECRET, (st, "what was saved for Plex is not what was typed"))
st, j = call("/api/setup/test", {"name": "Plex"}); check("Test asks Plex with what is saved and answers in a sentence", st == 200 and j["ok"] and j["note"] == "Plex answered: 3 libraries (Films, Music, Shows).", j)
sent = json.dumps([call("/api/settings")[1], call("/api/setup")[1], call("/api/state")[1], call("/api/about")[1], call("/api/actions")[1]]); items = {i["name"]: i for i in call("/api/settings")[1]["items"]}
check("Plex's token never comes back to the page: not in the settings, setup, state, About or actions answers; only that one is saved", PLEX_SECRET not in sent and items["PLEX_TOKEN"]["value"] == "" and items["PLEX_TOKEN"]["saved"] is True, "the Plex secret was found in an answer, or was not marked as saved")
check("the change is in the actions log by name", any(a["kind"] == "settings" and "PLEX_TOKEN" in a["result"] and "PLEX_URL" in a["result"] for a in d.actions(c)))
d.scan(now=now + 4); rows = by(); px = next(k for k in call("/api/setup")[1]["connections"] if k["name"] == "Plex")
check("the next scan asks Plex: the library folder Plex does not look in is named, and Setup shows that Plex answered", rows[P["px_outside"]]["code"] == "NOT_IN_PLEX_FOLDER" and rows[P["px_unindexed"]]["code"] == "NOT_IN_PLEX" and px["status"] == "ok" and px["kind"] == "plex" and px["source"] == "page", (rows[P["px_outside"]]["code"], px["status"]))
st, j = call("/api/settings", {"set": {"PLEX_URL": "http://127.0.0.1:9/elsewhere"}}); items = {i["name"]: i for i in call("/api/settings")[1]["items"]}
check("a new Plex address forgets the saved token, so the token is never sent to an address it was not typed for", st == 200 and items["PLEX_TOKEN"]["saved"] is False and d.PLEX_TOKEN == "", "the Plex secret was still marked saved, or still in memory, after the address changed")
os.environ["PLEX_URL"] = f"{M}/plex"; st, j = call("/api/settings", {"set": {"PLEX_TOKEN": "typed-on-the-page"}}); items = {i["name"]: i for i in call("/api/settings")[1]["items"]}
check("with Plex's address set on the container, the page changes neither the address nor the token", st == 409 and "container" in j["error"] and items["PLEX_URL"]["locked"] and items["PLEX_TOKEN"]["locked"], (st, "the Plex rows were not locked"))
os.environ.pop("PLEX_URL"); call("/api/settings", {"reset": ["PLEX_URL", "PLEX_TOKEN"]}); d.scan(now=now + 5)
check("Use the default takes Plex away again: the next scan has no Plex verdicts", d.PLEX_URL == "" and not any(i["code"] in d.PLEX_CODES for i in by().values()) and d.PLEX_STATUS == {})
st, j = call("/api/setup/test", {"name": "Nonsense"}); check("an unknown app is answered in words", not j["ok"] and "does not know" in j["note"])
# who may write: without a sign-in only a direct browser on your own network; through a proxy nobody; the demo never
st, j = call("/api/settings", {"set": {"RECYCLE_DAYS": "7"}}, {"X-Forwarded-For": "203.0.113.9"})
check("without a sign-in, a change through a reverse proxy is refused in words and nothing changes", st == 403 and "own network" in j["error"] and d.RECYCLE_DAYS == 30, (st, j))
st, j = call("/api/settings", headers={"X-Forwarded-For": "203.0.113.9"}); check("the page is told it may not write, and why", j.get("writable") is False and "Protect the page" in j.get("why_not", ""))
d.save_account("owner", "a long password"); d.AUTH_CACHE.pop("page", None)
st, j = call("/api/settings", {"set": {"RECYCLE_DAYS": "7"}}, {"X-Forwarded-For": "192.168.1.5"}); check("with sign-in on, a request without the cookie is refused", st == 401 and d.RECYCLE_DAYS == 30, (st, j))
d.save_account("", ""); d.SANDBOX = True
check("the public demo never changes settings", d.settings_writable("127.0.0.1", {})[0] is False); d.SANDBOX = False
# the shared-mount test (P4.1)
st, j = call("/api/setup"); m = j.get("mount", {})
check("the Setup answer carries the shared-mount test, what the arrs list and every connection", st == 200 and "lead" in m and j["discovered"]["found"] and {x["name"] for x in j["connections"]} >= {"Sonarr", "Deluge", "rTorrent"}, (st, list(j)))
check("on one disk: library and downloads on one mount, hardlinks proven by the scan", m.get("ok") is True and "one mount" in m["lead"] and any("hardlinks work" in x["text"] for x in m["lines"]), m)
check("Sonarr's history moves files under one top folder here, and the test says so", any(x["level"] == "ok" and "Sonarr" in x["text"] and "one folder" in x["text"] for x in m["lines"]), m["lines"])
two = [{"point": "/", "root": "/", "fs": "overlay"}, {"point": DATA + "/media", "root": "/media", "fs": "shfs"}, {"point": DATA + "/torrents", "root": "/torrents", "fs": "shfs"}]
m2 = d.mount_test(c, two)
check("two mounts: the test says they can never be one file and gives the one-line change", m2["ok"] is False and "two separate mounts" in m2["lead"] and any("-v /mnt/user/data:" in x["text"] for x in m2["lines"]), m2)
d.LAYOUT.clear(); d.LAYOUT["sonarr"] = {"from": "/downloads", "to": "/tv", "n": 9, "all": 9}; m3 = d.mount_test(c)
check("an arr that imports from /downloads into /tv is told the one-line change for its own container", m3["ok"] is False and any("Sonarr takes its downloads from /downloads" in x["text"] and "one mount for both" in x["text"] for x in m3["lines"]), m3["lines"])
check("every sentence of the test ends with a full stop", all(x["text"].endswith(".") for x in m2["lines"] + m3["lines"]) and m2["lead"].endswith(".") and m3["lead"].endswith("."))
d.LAYOUT.clear(); c2 = d.sqlite3.connect(":memory:"); c2.row_factory = d.sqlite3.Row; c2.executescript("CREATE TABLE file(path TEXT, dev INTEGER, kind TEXT); CREATE TABLE verdict(code TEXT); CREATE TABLE kv(k TEXT, v TEXT);")
check("before any scan the test says to scan first", d.mount_test(c2, two)["ok"] is None and "Scan first" in d.mount_test(c2, two)["lead"])
srv.shutdown(); msrv.shutdown(); c.close(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
