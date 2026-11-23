"""The morning message (P2.6): pinned word for word on the fake disk, delivered to a mock ntfy with the right headers, served at /api/digest.
A changed wording is a deliberate change: run with --update to re-pin tests/golden/digest.txt.
Run: python tests/digest.py [--update]   Exit 1 on failure."""
import os, sys, json, time, tempfile, socket, threading, urllib.request, atexit, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-digest-"); DATA = os.path.join(TMP, "data")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)   # the fake disk goes when the test ends, pass or fail (#36)
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100", PUBLIC_URL="https://dedupearr.example.test/")
for k in ("NTFY_URL", "NTFY_TOKEN", "NTFY_TOKEN_FILE", "DIGEST", "SANDBOX"): os.environ.pop(k, None)
import fake_disk  # noqa: E402
fake_disk.build(DATA)
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:400]}]"))

c = d.db(); t0 = d.digest(c)
check("before any scan the message says so and asks for nothing", t0["lines"][0] == "No scan has run yet." and t0["needs"] == 0 and t0["title"] == "Dedupearr daily: nothing needs you", t0)
d.scan(c); now = time.time()
def held(orig, size, at, restored=None, purged=None):
    c.execute("INSERT INTO held(orig,held,size,dev,ino,at,kind,why,restored,purged,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (orig, orig + ".held", size, 1, 1, at, "bin", "test", restored, purged, ""))
held(f"{DATA}/x/Fresh.Show.S01E01.mkv", 4_200_000_000, now - 3600)                                        # moved last night, waits
held(f"{DATA}/x/Fresh.Show.S01E02.mkv", 1_000_000_000, now - 7200, restored=now - 1800)                   # moved and put back
held(f"{DATA}/x/Old.Movie.2019.mkv", 6_000_000_000, now - (d.HOLD_DAYS + 1) * 86400, purged=now - 900)    # time ran out last night
held(f"{DATA}/x/Last.Day.2020.mkv", 2_000_000_000, now - (d.HOLD_DAYS - 0.5) * 86400)                     # leaves within a day
held(f"{DATA}/x/Long.Ago.2018.mkv", 9_000_000_000, now - 90 * 86400, purged=now - 60 * 86400)             # old history: not in "since yesterday"
c.commit(); snap = lambda: ([tuple(r) for r in c.execute("SELECT * FROM held ORDER BY id")], [tuple(r) for r in c.execute("SELECT * FROM verdict ORDER BY path")], c.execute("SELECT COUNT(*) FROM action").fetchone()[0])  # noqa: E731
before = snap(); t = d.digest(c); after = snap(); text = t["title"] + "\n" + t["text"]
gold = os.path.join(ROOT, "tests", "golden", "digest.txt")
if "--update" in sys.argv: open(gold, "w").write(text + "\n"); print("pinned:\n" + text); sys.exit(0)
want = open(gold).read().rstrip("\n") if os.path.exists(gold) else None
check("the message matches the pinned text (tests/golden/digest.txt)", want is not None and text == want, "\n--- got ---\n" + text + "\n--- want ---\n" + str(want))
s = d.state(c); needs = [v for v in s["items"] if v["needs_you"]]
check("the count and the size are the page's own numbers", t["needs"] == len(needs) and t["reclaim_bytes"] == s["reclaim_bytes"] and t["decide_bytes"] == s["decide_bytes"], (t["needs"], len(needs)))
check("what can go right now is never more than what there is to get back", 0 < t["now_bytes"] <= t["reclaim_bytes"], (t["now_bytes"], t["reclaim_bytes"]))
check("an ignored file is not counted", True if not needs else (c.execute("INSERT OR REPLACE INTO ignored(path, at) VALUES(?,?)", (needs[0]["path"], now)), c.commit(), d.digest(c)["needs"] == len(needs) - 1)[2])
c.execute("DELETE FROM ignored"); c.commit()
check("since yesterday counts last night's moves, put-backs and final deletes, not old history", "Since yesterday: 2 files moved to the holding bin (5.2 GB), 1 put back, 1 deleted for good (6 GB back)" in t["lines"], t["lines"])
check("the holding bin line warns about files that leave within a day", any(line.startswith("Holding bin: 2 files, 6.2 GB; 1 will be deleted for good within a day") for line in t["lines"]), t["lines"])
check("a clean night says so", t["lines"][-1] == "No errors since yesterday" and t["priority"] == "default", t["lines"][-1])
check("no path, address or secret in the message", DATA not in text and "192.168." not in text and "apikey" not in text.lower() and "http" not in text, text)
check("reading the message changes nothing", before == after and after[2] == 0)

# ---- delivery: a mock ntfy receives exactly this text with a title, a priority, a link and the token
got = []
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        got.append(dict(headers={k: v for k, v in self.headers.items()}, body=self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())); self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
check("without a topic nothing is sent", d.send_digest(c) is False and not got)
d.NTFY_URL = f"http://127.0.0.1:{srv.server_address[1]}/dedupearr"; d.NTFY_TOKEN = "tk_test"
check("with a topic the nightly message is delivered", d.send_digest(c) is True and len(got) == 1, got)
g = got[0] if got else {"headers": {}, "body": ""}; title = g["headers"].get("Title", "").encode("latin-1").decode("utf-8")
check("the body is the message, unchanged", g["body"] == t["text"], g["body"])
check("the title carries the count and the size", title == t["title"] and "need you" in title and "to get back" in title, title)
check("the message links to the page (PUBLIC_URL) and carries the token", g["headers"].get("Click") == "https://dedupearr.example.test/" and g["headers"].get("Authorization") == "Bearer tk_test", g["headers"])
d.DIGEST = "off"; check("DIGEST=off sends nothing", d.send_digest(c) is False and len(got) == 1); d.DIGEST = "on"
d.SANDBOX = True; check("the public demo never sends", d.send_digest(c) is False and d.ntfy("x", "y") is False and len(got) == 1); d.SANDBOX = False

# ---- trouble: a failed scan and an unreadable client raise the priority and are named
c.execute("INSERT INTO run(started,finished,files,bytes,wasted,note) VALUES(?,?,?,?,?,?)", (now, now + 1, 0, 0, 0, "failed: disk gone")); c.commit(); d.CLIENT_STATUS["Deluge"] = {"ok": False, "n": 0, "note": "refused"}
try: c.execute("INSERT OR REPLACE INTO kv(k,v) VALUES('client_status', ?)", (json.dumps(d.CLIENT_STATUS),)); c.commit()
except Exception: pass
t2 = d.digest(c)
check("a failed scan is named and raises the priority", t2["priority"] == "high" and "the last scan failed" in t2["lines"][-1], t2["lines"][-1])
check("a client that could not be read is named", "Deluge could not be read" in t2["lines"][-1], t2["lines"][-1])
d.NTFY_URL = "http://127.0.0.1:9/none"; ok = d.send_digest(c)
check("a topic that does not answer is an error on About, not a crash", ok is False and any("morning message" in e["text"] for e in d.errors_recent()), d.errors_recent()[-1:])

# ---- the endpoint Auditarr reads
s_ = socket.socket(); s_.bind(("127.0.0.1", 0)); port = s_.getsockname()[1]; s_.close(); d.serve(port, block=False)
j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/digest", timeout=10).read())
check("/api/digest serves title, lines, text and the numbers", {"title", "lines", "text", "priority", "needs", "reclaim_bytes", "reclaim_h", "trouble"} <= set(j) and j["text"] == "\n".join(j["lines"]), list(j))
src = open(os.path.join(ROOT, "dedupearr.py")).read()
check("the scheduler sends the message after the nightly scan", "try: scan()\n        except Exception: pass\n        try: send_digest()" in src)
srv.shutdown(); c.close(); bad = results.count(False); print(f"\n{len(results) - bad} of {len(results)} checks passed"); sys.exit(1 if bad else 0)
