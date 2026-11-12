"""Sign-in: off without AUTH_USER/AUTH_PASSWORD; with them the page asks, except the container itself, TRUST_PROXY_AUTH's header,
and (LOCAL_NO_LOGIN=1) a browser on your own network talking to the port directly. Through any reverse proxy it always asks.
Run: python tests/auth.py   Exit 1 on failure."""
import os, sys, json, tempfile, socket, urllib.request, urllib.error, urllib.parse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="dedupearr-auth-"); os.makedirs(os.path.join(TMP, "data"))
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=os.path.join(TMP, "data"), AUTH_USER="", AUTH_PASSWORD="", LOCAL_NO_LOGIN="0", TRUST_PROXY_AUTH="0")
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:200]}]"))
class Fake:
    def __init__(self, ip, headers=None): self.client_address = (ip, 1); self.headers = dict(headers or {})
auth = d.Handler._auth

check("no AUTH_USER/AUTH_PASSWORD: the page is open", auth(Fake("8.8.8.8")) == "")
d.AUTH_USER, d.AUTH_PASSWORD = "owner", "correct horse battery"
check("sign-in on: a public address is asked", auth(Fake("8.8.8.8")) is None)
check("sign-in on: the LAN is asked too while LOCAL_NO_LOGIN is off", auth(Fake("192.168.1.20")) is None)
check("sign-in on: the container itself (health checks, the page bot) gets in", auth(Fake("127.0.0.1")) == "local")
check("sign-in on: loopback through a proxy on the same host is asked", auth(Fake("127.0.0.1", {"X-Forwarded-For": "203.0.113.4"})) is None)
ck = "dedupearr_s=" + d.session_cookie()
check("a valid cookie names the user", auth(Fake("8.8.8.8", {"Cookie": ck})) == "owner")
check("a tampered cookie is refused", auth(Fake("8.8.8.8", {"Cookie": ck[:-3] + ("aaa" if not ck.endswith("aaa") else "bbb")})) is None)
check("an expired cookie is refused", auth(Fake("8.8.8.8", {"Cookie": "dedupearr_s=" + d.session_cookie(days=-1)})) is None)
d.AUTH_PASSWORD = "a new password 2"; check("changing AUTH_PASSWORD signs every browser out", auth(Fake("8.8.8.8", {"Cookie": ck})) is None); d.AUTH_PASSWORD = "correct horse battery"
check("the proxy header is ignored unless TRUST_PROXY_AUTH=1", auth(Fake("172.25.0.9", {"Remote-User": "alice", "X-Forwarded-For": "1.2.3.4"})) is None)
d.TRUST_PROXY_AUTH = True; check("TRUST_PROXY_AUTH=1: the proxy's Remote-User counts", auth(Fake("172.25.0.9", {"Remote-User": "alice", "X-Forwarded-For": "1.2.3.4"})) == "alice"); d.TRUST_PROXY_AUTH = False
d.LOCAL_NO_LOGIN = True
check("LOCAL_NO_LOGIN=1: a direct LAN browser gets in", auth(Fake("192.168.1.20")) == "lan")
check("LOCAL_NO_LOGIN=1: a tailnet address gets in", auth(Fake("100.101.5.7")) == "lan")
check("LOCAL_NO_LOGIN=1: an IPv6 ULA address gets in", auth(Fake("fd00::12")) == "lan")
check("LOCAL_NO_LOGIN=1: a public address is still asked", auth(Fake("8.8.8.8")) is None)
for h in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP", "Forwarded", "X-Forwarded-Host", "Tailscale-Funnel-Request"):
    check(f"LOCAL_NO_LOGIN=1: through a proxy ({h}) from a private address is still asked", auth(Fake("172.25.0.9", {h: "203.0.113.4"})) is None)
check("LOCAL_NO_LOGIN=1: a garbage address is not local", auth(Fake("not-an-ip")) is None)
check("the right password passes, a wrong one or a wrong user does not", d.check_password("owner", "correct horse battery") and not d.check_password("owner", "nope") and not d.check_password("eve", "correct horse battery"))
d.LOCAL_NO_LOGIN = False

# ---- over HTTP, as a browser behind a proxy (the header makes 127.0.0.1 count as the outside)
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); srv = d.serve(port, block=False); base = f"http://127.0.0.1:{port}"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
op = urllib.request.build_opener(NoRedirect)
def req(p, data=None, headers=None, json_body=None):
    h = {"X-Forwarded-For": "203.0.113.4", **(headers or {})}
    if json_body is not None: data = json.dumps(json_body).encode(); h["Content-Type"] = "application/json"
    try: r = op.open(urllib.request.Request(base + p, data=data, headers=h), timeout=10); return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()
st, h, b = req("/"); check("the page sends a stranger to the sign-in", st == 303 and h.get("Location") == "login", (st, h.get("Location")))
st, h, b = req("/api/state"); check("the API answers 401 to a stranger", st == 401 and b"sign in" in b, st)
st, h, b = req("/api/ignore", json_body={"path": "/x"}); check("a write from a stranger is refused", st == 401, st)
st, h, b = req("/health"); check("/health stays open for monitoring", st in (200, 503) and b"version" in b, st)
st, h, b = req("/login"); check("the sign-in page shows, with the tab icon", st == 200 and b'name="password"' in b and b'rel="icon"' in b, st)
st, h, b = req("/login", data=urllib.parse.urlencode({"user": "owner", "password": "wrong"}).encode()); check("a wrong password shows the page again with 401", st == 401 and b"Wrong user or password" in b, st)
st, h, b = req("/login", data=urllib.parse.urlencode({"user": "owner", "password": "correct horse battery"}).encode())
cookie = (h.get("Set-Cookie") or "").split(";")[0]; check("the right password sets an HttpOnly cookie and goes to the page", st == 303 and cookie.startswith("dedupearr_s=") and "HttpOnly" in h.get("Set-Cookie", ""), (st, h.get("Set-Cookie")))
st, h, b = req("/api/state", headers={"Cookie": cookie}); j = json.loads(b) if st == 200 else {}
check("with the cookie the page loads and knows sign-in is on", st == 200 and j.get("auth") == {"enabled": True, "via": ""}, (st, j.get("auth")))
st, h, b = req("/logout", headers={"Cookie": cookie}); check("Sign out clears the cookie", st == 303 and "Max-Age=0" in h.get("Set-Cookie", ""), h.get("Set-Cookie"))
ui = open(os.path.join(ROOT, "ui.html")).read(); check("the page has its icon in the tab", 'rel="icon" type="image/png" href="data:image/png;base64,' in ui)
srv.shutdown(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
