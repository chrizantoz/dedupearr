"""The answer must not depend on the order the disk lists folders. ext4, btrfs and XFS hand back the same folder in different orders, so a
verdict that takes "the first file of that size" is right on one machine and wrong on the next (found 2026-11-22: a leftover TV episode was
filed under Movies and named an unrelated film of the same size, only on CI). This runs the same fake disk with every folder listed A to Z,
then Z to A, and compares each file's verdict, category, twin and evidence. Run: python tests/scan_order.py   Exit 1 on failure."""
import os, re, sys, json, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) > 1:   # child: one scan with the folders listed in the given order, answers printed as JSON
    import time, tempfile, socket, atexit, shutil
    rev = sys.argv[1] == "za"; real = os.scandir
    class Listed:
        def __init__(self, p): self.items = sorted(real(p), key=lambda e: e.name, reverse=rev)
        def __iter__(self): return iter(self.items)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def close(self): pass
    os.scandir = lambda p=".": Listed(p)
    sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
    TMP = tempfile.mkdtemp(prefix="dedupearr-order-"); DATA = os.path.join(TMP, "data")
    atexit.register(shutil.rmtree, TMP, ignore_errors=True)   # the fake disk goes when the test ends, pass or fail (#36)
    import fake_disk, mock_clients
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); M = f"http://127.0.0.1:{port}"
    now = time.time(); P = fake_disk.build(DATA, now); P.update(mock_clients.extra_files(DATA, fake_disk.blob)); mock_clients.start(mock_clients.world_for(P, DATA), port)
    os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100", CLIENT_TIMEOUT="5", **mock_clients.env_for(M))
    import dedupearr as d
    d.scan(now=now); rel = lambda p: (p or "").replace(DATA, "")
    out = {rel(i["path"]): [i["code"], i["category"], rel(i.get("twin")), rel(i["evidence"]).replace(str(port), "PORT")] for i in d.state(d.db())["items"]}
    sys.stderr.write("ANSWERS " + json.dumps(out) + "\n"); shutil.rmtree(TMP, ignore_errors=True); os._exit(0)
def run(order):
    r = subprocess.run([sys.executable, os.path.abspath(__file__), order], capture_output=True, text=True, timeout=300)
    line = [x for x in r.stderr.splitlines() if x.startswith("ANSWERS ")]
    if not line: print(r.stdout[-800:], r.stderr[-800:]); sys.exit(1)
    return json.loads(line[-1][8:])
inode = lambda t: re.sub(r"inodes? \d+( and \d+)?|inode \d+", "inode N", t)   # inode numbers differ between two disks by nature
az, za = run("az"), run("za"); bad = []
for path in sorted(set(az) | set(za)):
    a, z = az.get(path), za.get(path)
    if a is None or z is None or a[:3] != z[:3] or inode(a[3]) != inode(z[3]): bad.append((path, a, z))
for path, a, z in bad: print(f"FAIL {path}\n   A to Z: {a}\n   Z to A: {z}")
print(f"{len(az) - len(bad)} files answered the same in both orders, {len(bad)} did not"); sys.exit(1 if bad or not az else 0)
