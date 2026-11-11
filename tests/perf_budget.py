"""Performance budget: judging 60,000 recorded files (a large library) must stay under a few seconds, so a nightly scan is dominated by
the disk walk, never by the verdict logic. Inserts rows straight into the database (no real files) and times judge().
Run: python tests/perf_budget.py   Exit 1 when over budget."""
import os, sys, time, random, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
os.environ.update(CONFIG_DIR=tempfile.mkdtemp(prefix="dedupearr-perf-"), SCAN_ROOTS="/nonexistent")
import dedupearr as d  # noqa: E402
N = 60000; rnd = random.Random(1); c = d.db(); now = time.time(); rows = []
for i in range(N):
    kind = "library" if i % 25 else "download"; ino = i if i % 40 else i - 1   # every 40th download shares an inode with the file before it
    size = rnd.randint(500_000_000, 60_000_000_000) if i % 7 else 4_000_000_000   # a cluster of equal sizes, like a season of one release
    rows.append((f"/data/{'media/tv' if kind == 'library' else 'torrents/tv'}/Show {i // 24}/Ep.S01E{i % 24:02d}.mkv", 1, ino, 2 if ino != i else 1, size, now - 86400, kind, now))
c.executemany("INSERT OR REPLACE INTO file(path,dev,ino,nlink,size,mtime,kind,seen) VALUES(?,?,?,?,?,?,?,?)", rows); c.commit()
d.fingerprint = lambda c, row, want_full, budget: ("x:y:z", None, budget)   # no real files: fingerprints are stubbed
t0 = time.time(); wasted = d.judge(c, now); dt = time.time() - t0
n = c.execute("SELECT count(*) FROM verdict").fetchone()[0]
t1 = time.time(); S = d.state(c); dt2 = time.time() - t1
ok1 = dt < 8.0; ok2 = dt2 < 4.0
print(f"{'PASS' if ok1 else 'FAIL'} judge() over {N} files in {dt:.2f} s (budget 8 s), {n} verdicts, {d.human(wasted)} wasted")
print(f"{'PASS' if ok2 else 'FAIL'} state() for the page in {dt2:.2f} s (budget 4 s), {len(S['items'])} items")
sys.exit(0 if ok1 and ok2 else 1)
