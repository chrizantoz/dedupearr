"""The public demo (SANDBOX=1): the page knows it is the demo, the health says so, and writes are rate-limited per visitor.
Run: python tests/sandbox_mode.py   Exit 1 on failure."""
import os, sys, json, time, tempfile, socket, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-sbx-"); DATA = os.path.join(TMP, "data")
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", SANDBOX="1", SANDBOX_RATE="5")
import fake_disk  # noqa: E402
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:200]}]"))
fake_disk.build(DATA); s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = d.serve(port, block=False); base = f"http://127.0.0.1:{port}"
def post(p, body=None, ip=None):
    req = urllib.request.Request(base + p, data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json", **({"X-Forwarded-For": ip} if ip else {})}, method="POST")
    try: r = urllib.request.urlopen(req, timeout=10); return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
st, j = post("/api/scan"); time.sleep(1.5)
state = json.loads(urllib.request.urlopen(base + "/api/state", timeout=10).read())
check("the state says it is the demo and carries the note", state["sandbox"] is True and "resets on the hour" in state["sandbox_note"], state.get("sandbox_note"))
about = json.loads(urllib.request.urlopen(base + "/api/about", timeout=10).read())
check("About health names the demo", any("public demo" in h["text"] for h in about["health"]))
codes = [post("/api/ignore", {"path": "/x"}, ip="203.0.113.9")[0] for _ in range(7)]
check("a visitor gets SANDBOX_RATE writes a minute, then 429 with a plain message", codes[:5] == [200] * 5 and codes[5] == 429, codes)
st2, j2 = post("/api/ignore", {"path": "/x"}, ip="203.0.113.9"); check("the 429 says what to do", st2 == 429 and "slow down" in j2["error"], j2)
check("another visitor is not affected", post("/api/ignore", {"path": "/x"}, ip="203.0.113.10")[0] == 200)
check("reads are never limited", all(urllib.request.urlopen(base + "/api/state", timeout=10).status == 200 for _ in range(10)))
check("the page still says read-only (the demo changes nothing either)", state["read_only"] is True)
srv.shutdown(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
