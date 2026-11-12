"""Fuzz the client and arr answers: the fake clients reply with randomly wounded JSON (keys gone, nulls, wrong types, huge strings).
The scan must finish every time, verdict codes must stay valid, and nothing may be called waste without a size.
Run: python tests/fuzz_clients.py [rounds=12] [seed=7]   Exit 1 on any broken rule or crash."""
import os, sys, time, tempfile, socket
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-fuzzc-"); DATA = os.path.join(TMP, "data")
import fake_disk, mock_clients  # noqa: E402
N = int(sys.argv[1]) if len(sys.argv) > 1 else 12; SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 7
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); M = f"http://127.0.0.1:{port}"
P = fake_disk.build(DATA); P.update(mock_clients.extra_files(DATA, fake_disk.blob)); world = mock_clients.world_for(P, DATA)
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100", CLIENT_TIMEOUT="5", **mock_clients.env_for(M))
import dedupearr as d  # noqa: E402
bad = 0; crashed = 0
for n in range(N):
    srv = mock_clients.start(world, port, mangle_seed=SEED + n)
    try:
        r = d.scan(now=time.time() + n)
    except Exception as e:
        crashed += 1; print(f"FAIL round {n}: scan crashed on a wounded answer: {e!r}"); srv.shutdown(); srv.server_close(); continue
    c = d.db(); S = d.state(c); c.close(); srv.shutdown(); srv.server_close()
    if r is None or S["last_run"]["note"] != "ok": bad += 1; print(f"FAIL round {n}: scan did not end ok: {S['last_run']}")
    for i in S["items"]:
        if i["code"] not in d.LABEL: bad += 1; print(f"FAIL round {n}: unknown code {i['code']}")
        if i["wasted"] not in (0, i["size"]): bad += 1; print(f"FAIL round {n}: wasted is neither 0 nor the size: {i['path']}")
        if not i["plain"].endswith(".") or "None" in i["plain"] or "nan" in i["plain"].lower(): bad += 1; print(f"FAIL round {n}: sentence broke: {i['plain'][-80:]!r}")
        if i["client_text"] and ("None" in i["client_text"] or "nan" in i["client_text"]): bad += 1; print(f"FAIL round {n}: client line broke: {i['client_text']!r}")
    if S["now_bytes"] > S["reclaim_bytes"] + S["decide_bytes"]: bad += 1; print(f"FAIL round {n}: 'can go now' exceeds the waste")
print(f"\n{N} rounds of wounded client answers, {crashed} crash(es), {bad} broken rule(s)"); sys.exit(1 if bad or crashed else 0)
