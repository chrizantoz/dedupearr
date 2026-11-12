"""Edge-case worlds for the UI bots, derived from the base fixtures: a library with nothing wrong, a huge one, the app unreachable,
and a scan that failed. Writes uibot/fixtures-edge/<name>/{state,about}.json plus edge.json naming the case.
Usage: python3 uibot/edge_fixtures.py"""
import copy, json, os, random
HERE = os.path.dirname(os.path.abspath(__file__)); SRC = os.path.join(HERE, "fixtures"); DST = os.path.join(HERE, "fixtures-edge")
base = {n: json.load(open(os.path.join(SRC, n + ".json"))) for n in ("state", "about")}
def write(name, d, case=None):
    os.makedirs(os.path.join(DST, name), exist_ok=True)
    for k, v in d.items(): json.dump(v, open(os.path.join(DST, name, k + ".json"), "w"))
    json.dump({"case": case or name}, open(os.path.join(DST, name, "edge.json"), "w"))

# 1. tidy: every file is a real copy or the only copy; nothing needs a decision (this is what success looks like)
e = copy.deepcopy(base); s = e["state"]
s["items"] = [i for i in s["items"] if not i["needs_you"]]
s["counts"] = {}; s["wasted_by"] = {}; s["wasted_by_h"] = {}
for i in s["items"]: s["counts"][i["code"]] = s["counts"].get(i["code"], 0) + 1; s["wasted_by"][i["code"]] = 0; s["wasted_by_h"][i["code"]] = "0 B"
s.update(reclaim_bytes=0, reclaim_h="0 B", decide_bytes=0, decide_h="0 B", lead=f"Your library is {s['library_h']}. Nothing is wasted: every file is the real copy or a hardlink.")
write("tidy", e)

# 2. empty: a fresh install whose first scan found no video files at all
e = copy.deepcopy(base); s = e["state"]
s.update(items=[], counts={}, wasted_by={}, wasted_by_h={}, reclaim_bytes=0, reclaim_h="0 B", decide_bytes=0, decide_h="0 B", library_bytes=0, library_h="0 B", lead="Your library is 0 B. 0 B is wasted on second copies and old recycle-bin files.")
s["last_run"] = dict(s["last_run"], files=0, bytes=0, wasted=0)
write("empty", e)

# 3. huge: 3,000 files that need a decision, long names, every verdict
e = copy.deepcopy(base); s = e["state"]; rnd = random.Random(3)
codes = [c for c in s["labels"]]; items = []
for n in range(3000):
    src = rnd.choice(base["state"]["items"]); i = copy.deepcopy(src)
    i["path"] = f"/data/torrents/tv/Show {n:04d}/Some.Very.Long.Release.Name.S{n % 40:02d}E{n % 24:02d}.2160p.WEB-DL.DDP5.1.Atmos.H.265-GROUP{n}.mkv"
    i["title"] = f"Some Very Long Release Name {n} S{n % 40:02d}E{n % 24:02d}"; i["code"] = codes[n % len(codes)]; i["label"] = s["labels"][i["code"]]
    i["needs_you"] = i["code"] in ("DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN", "BIN_OLD", "OTHER"); i["ignored"] = False
    i["size"] = rnd.randint(500_000_000, 60_000_000_000); i["wasted"] = i["size"] if i["needs_you"] else 0; i["size_h"] = f"{i['size'] / 1e9:.1f} GB"; i["wasted_h"] = f"{i['wasted'] / 1e9:.1f} GB" if i["wasted"] else "0 B"
    items.append(i)
s["items"] = items; s["counts"] = {}; s["wasted_by"] = {}
for i in items: s["counts"][i["code"]] = s["counts"].get(i["code"], 0) + 1; s["wasted_by"][i["code"]] = s["wasted_by"].get(i["code"], 0) + i["wasted"]
s["wasted_by_h"] = {k: f"{v / 1e12:.1f} TB" for k, v in s["wasted_by"].items()}
s["last_run"] = dict(s["last_run"], files=len(items)); write("huge", e)

# 4. down: the app does not answer (the bot's fetch returns 503 for everything)
write("down", copy.deepcopy(base))

# 5. errors: the last scan failed, a root is missing, recent errors are listed
e = copy.deepcopy(base); s = e["state"]; a = e["about"]
s["last_run"] = dict(s["last_run"], note="failed: [Errno 13] Permission denied: '/data/torrents'", files=0)
a["health"] = [{"level": "warn", "text": "The last scan failed: [Errno 13] Permission denied: '/data/torrents'"}, {"level": "warn", "text": "Scan root /data/usenet is not mounted or not a folder."}, {"level": "warn", "text": "3 errors in the last 24 hours."}]
a["errors"] = [{"at": 1794438000, "text": "cannot list /data/torrents: [Errno 13] Permission denied"}] * 3
write("errors", e)
print("edge fixtures written: tidy, empty, huge, down, errors")
