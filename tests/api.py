"""The page's contract: start the real server on the fake disk, hit every endpoint, check shapes and the health rule.
Also writes uibot/fixtures/state.json and about.json so the UI bot tests the page against real answers.
Run: python tests/api.py   Exit 1 on failure."""
import os, sys, json, time, tempfile, socket, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-api-"); DATA = os.path.join(TMP, "data")
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100")
import fake_disk, mock_clients  # noqa: E402
_s = socket.socket(); _s.bind(("127.0.0.1", 0)); _mport = _s.getsockname()[1]; _s.close(); _M = f"http://127.0.0.1:{_mport}"
_P = fake_disk.build(DATA); _P.update(mock_clients.extra_files(DATA, fake_disk.blob)); _mock = mock_clients.start(mock_clients.world_for(_P, DATA), _mport)
os.environ.update(CLIENT_TIMEOUT="10", **mock_clients.env_for(_M))   # the recorded fixtures carry client states, so the UI bots see 'can go now'
import dedupearr as d  # noqa: E402
_JUDGED = len([k for k in _P if k not in ("small", "text")])   # every planted file except the one under MIN_SIZE_MB and the non-video one
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:220]}]"))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = d.serve(port, block=False); base = f"http://127.0.0.1:{port}"
def get(p):
    r = urllib.request.urlopen(base + p, timeout=10); return r.status, json.loads(r.read())
def post(p, body=None):
    req = urllib.request.Request(base + p, data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try: r = urllib.request.urlopen(req, timeout=10); return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
html = urllib.request.urlopen(base + "/", timeout=10).read().decode()
check("the page is served", "<title>Dedupearr</title>" in html)
st, j = get("/health"); check("health before any scan is 200 (nothing is wrong yet)", st == 200, (st, j))
st, j = get("/api/state"); check("state before a scan has the shape the page expects", st == 200 and j["items"] == [] and j["last_run"] is None and "lead" in j and j["read_only"] is True, j.get("lead"))
st, j = post("/api/scan"); check("scan starts from the page", st == 200 and j["ok"], j)
for _ in range(100):
    time.sleep(0.1); st, j = get("/api/state")
    if j["last_run"] and not j["scanning"]["on"]: break
check("scan finished and the state carries items", j["last_run"] and j["last_run"]["files"] == _JUDGED and len(j["items"]) == _JUDGED, (j["last_run"], len(j["items"])))
check("the fixtures carry at least one row that waits for seeding, with its reason", any(i.get("wait") for i in j["items"]))
check("the fixtures carry client states and at least one 'can go now'", any(i["client_text"] for i in j["items"]) and any(i["removable"] and i["needs_you"] for i in j["items"]))
item = next(i for i in j["items"] if i["code"] == "DUPLICATE_COPY")
need = {"path", "code", "label", "title", "plain", "evidence", "size", "wasted", "size_h", "wasted_h", "twin", "twin_title", "needs_you", "ignored", "kind", "client_text", "removable"}
check("every item has every field the page reads", all(need <= set(i) for i in j["items"]), need - set(item))
check("counts, wasted_by and labels agree on codes", set(j["counts"]) <= set(j["labels"]) and set(j["wasted_by"]) == set(j["counts"]))
st, j2 = post("/api/ignore", {"path": item["path"]}); check("ignore answers in plain words", st == 200 and "hidden" in j2["note"], j2)
st, j3 = get("/api/state"); it = next(i for i in j3["items"] if i["path"] == item["path"])
check("an ignored item leaves Needs you but stays in the list", it["ignored"] and not it["needs_you"])
st, j4 = post("/api/unignore", {"path": item["path"]}); st, j5 = get("/api/state")
check("un-ignore brings it back", next(i for i in j5["items"] if i["path"] == item["path"])["needs_you"])
st, j6 = post("/api/ignore", {}); check("ignore without a path is refused with a question, not a crash", st == 400 and "?" in j6["error"], (st, j6))
st, j7 = get("/api/about"); check("about has health lines, settings without secrets, runs", st == 200 and j7["health"] and "SCAN_ROOTS" in j7["settings"] and j7["runs"], list(j7))
check("about carries the recent errors list and a health line about them", isinstance(j7["errors"], list) and any("errors in the last 24 hours" in h["text"] for h in j7["health"]), j7["health"])
check("about never echoes a key or password", not any(k.lower().endswith(("key", "token", "password", "secret")) for k in j7["settings"]))
st, j8 = get("/health"); check("health after a good scan is 200", st == 200 and j8["ok"])
json.dump({"ts": "2026-11-12T06:20", "passed": 30, "total": 36, "failed": ["a check"], "revision": "aaaaaaa1", "page_revision": "aaaaaaa1"}, open(os.path.join(os.environ["CONFIG_DIR"], "uibot.json"), "w"))
try: urllib.request.urlopen(base + "/health", timeout=10); check("health is 503 when the nightly page bot failed", False)
except urllib.error.HTTPError as e: check("health is 503 when the nightly page bot failed", e.code == 503)
st, j9 = get("/api/about"); check("About says the nightly check did not pass and names the failing check", any("did not pass" in h["text"] and "a check" in h["text"] for h in j9["health"]))
json.dump({"ts": "2026-11-12T06:20", "passed": 36, "total": 36, "failed": [], "revision": "aaaaaaa1", "page_revision": "bbbbbbb2"}, open(os.path.join(os.environ["CONFIG_DIR"], "uibot.json"), "w"))
st, j10 = get("/api/about"); check("a bot from another build is flagged even when it passed (drift guard)", any("pull both images" in h["text"] for h in j10["health"]))
os.remove(os.path.join(os.environ["CONFIG_DIR"], "uibot.json"))
try: urllib.request.urlopen(base + "/api/nothing", timeout=10); check("unknown routes answer 404", False)
except urllib.error.HTTPError as e: check("unknown routes answer 404", e.code == 404, e.code)
# fixtures for the UI bot
fx = os.path.join(ROOT, "uibot", "fixtures"); os.makedirs(fx, exist_ok=True)
st, state_json = get("/api/state")
for i in state_json["items"]: i["path"] = i["path"].replace(DATA, "/data"); i["twin"] = i["twin"].replace(DATA, "/data") if i["twin"] else None
state_json["roots"] = ["/data"]; [dk.__setitem__("root", "/data") for dk in state_json["disks"]]
json.dump(state_json, open(os.path.join(fx, "state.json"), "w"), indent=1, default=str)
j7["settings"]["SCAN_ROOTS"] = ["/data"]; json.dump(j7, open(os.path.join(fx, "about.json"), "w"), indent=1, default=str)
check("fixtures written for the UI bot", os.path.getsize(os.path.join(fx, "state.json")) > 2000)
# 2026-11-14: a real fix answered 'Failed to fetch' on the page and nothing recorded why. A fix that stops with an error answers in words and lands in Recent errors
_real = d.ui_fix; d.ui_fix = lambda c, path: (_ for _ in ()).throw(RuntimeError("disk said no"))
try: r9 = post("/api/fix", {"path": "/data/x.mkv"})
finally: d.ui_fix = _real
check("a fix that stops with an error answers 500 in words and is kept for the About tab", r9[0] == 500 and "disk said no" in str(r9[1]) and any("disk said no" in e["text"] for e in d.ERRORS), r9)
_ui = open(os.path.join(ROOT, "ui.html")).read()
check("after a done fix, a page refresh that fails is not shown as a failed fix", "The page could not refresh itself just now" in _ui)
srv.shutdown(); _mock.shutdown(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
