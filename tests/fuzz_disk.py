"""Fuzz the scanner: random folder trees (random kinds, sizes, hardlinks, copies, bins) must never crash it, and the verdicts must obey
the rules that define them. Run: python tests/fuzz_disk.py [worlds=25] [seed=7]   Exit 1 on any broken rule."""
import os, sys, random, shutil, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="dedupearr-fuzz-"); DATA = os.path.join(TMP, "data")
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="50")
import dedupearr as d  # noqa: E402
N = int(sys.argv[1]) if len(sys.argv) > 1 else 25; rnd = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
MB = 1024 * 1024; KINDS = {"library": "media/tv", "download": "torrents/tv", "recycle": "media/.trash", "other": "stuff"}
bad = 0; checked = 0
def fail(msg): global bad; bad += 1; print("FAIL " + msg)
for w in range(N):
    shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
    if os.path.exists(d.DB_PATH): os.remove(d.DB_PATH)
    expect = {}   # path -> set of allowed codes
    for n in range(rnd.randint(0, 14)):
        kind = rnd.choice(list(KINDS)); mb = rnd.choice([0, 1, 2, 2, 3]); seed = rnd.choice(["a", "b", "c", "d"])
        p = os.path.join(DATA, KINDS[kind], f"F{n}-{seed}-{mb}.mkv"); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f: f.write((seed.encode() * MB)[:MB] * mb if mb else b"tiny")
        if kind == "recycle": os.utime(p, (time.time() - rnd.choice([1, 40]) * 86400,) * 2)
        if mb == 0: continue
        expect[p] = kind
        if rnd.random() < 0.3:   # a hardlink into another kind
            k2 = rnd.choice(list(KINDS)); q = os.path.join(DATA, KINDS[k2], f"L{n}.mkv"); os.makedirs(os.path.dirname(q), exist_ok=True); os.link(p, q); expect[q] = k2
    now = time.time()
    try: r = d.scan(now=now)
    except Exception as e: fail(f"world {w}: scan crashed: {e!r}"); continue
    c = d.db(); S = d.state(c); by = {i["path"]: i for i in S["items"]}
    files = {row["path"]: dict(row) for row in c.execute("SELECT * FROM file")}
    groups = {}
    for p, row in files.items(): groups.setdefault((row["dev"], row["ino"]), []).append(p)
    if set(by) != set(expect): fail(f"world {w}: judged {len(by)} files, expected {len(expect)}: {set(by) ^ set(expect)}")
    if r["wasted"] > r["bytes"]: fail(f"world {w}: wasted {r['wasted']} > total {r['bytes']}")
    if S["library_bytes"] > r["bytes"]: fail(f"world {w}: library bytes {S['library_bytes']} > total")
    for p, v in by.items():
        checked += 1; row = files[p]; members = groups[(row["dev"], row["ino"])]; kinds = {files[m]["kind"] for m in members}
        if v["code"] in ("REAL_COPY", "LINKED_SEED") and not ({"library", "download"} <= kinds): fail(f"world {w}: {v['code']} without a library+download hardlink: {p}")
        if {"library", "download"} <= kinds and v["kind"] in ("library", "download") and v["code"] not in ("REAL_COPY", "LINKED_SEED"): fail(f"world {w}: shared inode across library and download but verdict {v['code']}: {p}")
        if v["code"] in ("DUPLICATE_COPY", "DUPLICATE_HEADER", "LOOKALIKE"):
            if not v["twin"] or files[v["twin"]]["size"] != row["size"]: fail(f"world {w}: {v['code']} twin missing or size differs: {p}")
            if v["code"] == "DUPLICATE_COPY" and open(p, "rb").read() != open(v["twin"], "rb").read(): fail(f"world {w}: DUPLICATE_COPY but content differs: {p}")
            if v["code"] == "LOOKALIKE" and open(p, "rb").read() == open(v["twin"], "rb").read(): fail(f"world {w}: LOOKALIKE but content identical: {p}")
        if v["code"] == "BIN_OLD" and (now - row["mtime"]) / 86400 <= d.RECYCLE_DAYS: fail(f"world {w}: BIN_OLD within the rule: {p}")
        if v["code"] == "BIN_RECENT" and (now - row["mtime"]) / 86400 > d.RECYCLE_DAYS: fail(f"world {w}: BIN_RECENT past the rule: {p}")
        if v["wasted"] not in (0, row["size"]): fail(f"world {w}: wasted is neither 0 nor the size: {p}")
        if v["wasted"] and v["code"] not in ("DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "BIN_OLD"): fail(f"world {w}: wasted on a verdict that is not waste: {v['code']} {p}")
        if not v["plain"].endswith(".") or "_" in v["label"]: fail(f"world {w}: sentence or label not plain: {v['label']!r} {v['plain'][-20:]!r}")
    d.scan(now=now + 5); S2 = d.state(c)
    if {i["path"]: i["code"] for i in S2["items"]} != {i["path"]: i["code"] for i in S["items"]}: fail(f"world {w}: a second scan of the same disk changed verdicts")
    c.close()
print(f"\n{N} random disks, {checked} verdicts checked, {bad} broken rule(s)"); sys.exit(1 if bad else 0)
