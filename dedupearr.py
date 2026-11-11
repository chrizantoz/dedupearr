#!/usr/bin/env python3
"""Dedupearr: the truth about your storage.

For every video file under your media and download folders it says what the file IS - the real copy, a hardlink that costs
nothing, a second copy that wastes space, a download nothing in the library matches, or an old recycle-bin entry - and shows
the evidence (inode, link count, size, checksum) in one plain sentence.

Phase 1 (this file): scan and tell. Read-only: nothing is moved, linked or deleted.
Phase 2: ask the download clients and the arrs, so an unmatched download becomes "leftover", "orphan" or "still seeding".
Phase 3: fixes behind a dry run, a holding bin and a 14-day undo.

Verdict codes (never shown raw; see LABEL and sentence()):
  REAL_COPY          library file and download file are one file with two names (hardlink)       wasted 0
  LIBRARY_ONLY       one copy, in the library                                                     wasted 0
  LIBRARY_TWIN       two names inside the library share one file                                  wasted 0
  LINKED_SEED        the download-folder name of a REAL_COPY                                      wasted 0 (hidden under its library row)
  DUPLICATE_COPY     library file and download file are two files with identical content          wasted size
  LOOKALIKE          same size as a library file, different content                               wasted 0
  DOWNLOAD_UNMATCHED no library file matches this download (phase 2 decides what it is)           wasted size, "to decide"
  BIN_OLD            in a recycle bin longer than RECYCLE_DAYS                                     wasted size
  BIN_RECENT         in a recycle bin, within the rule                                            wasted 0
  OTHER              in a folder that is neither library, downloads nor recycle bin               wasted 0
"""
import os, re, sys, json, time, shutil, sqlite3, hashlib, threading, datetime, argparse, traceback
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

APP = "Dedupearr"; TAGLINE = "the truth about your storage"; PHASE = 1
VERSION = os.environ.get("APP_VERSION", "dev"); REVISION = os.environ.get("APP_REVISION", "")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config"); os.makedirs(CONFIG_DIR, exist_ok=True)
DB_PATH = os.path.join(CONFIG_DIR, "dedupearr.db")
HTTP_PORT = int(os.environ.get("PORT", "3092"))
def _list(name, default): return [p.strip().rstrip("/") for p in os.environ.get(name, default).split(",") if p.strip()]
SCAN_ROOTS = _list("SCAN_ROOTS", "/data")
LIBRARY_DIRS = _list("LIBRARY_DIRS", ""); DOWNLOAD_DIRS = _list("DOWNLOAD_DIRS", ""); RECYCLE_DIRS = _list("RECYCLE_DIRS", "")
LIBRARY_HINTS = [h.lower() for h in _list("LIBRARY_HINTS", "media,library,movies,movie,films,tv,shows,series,anime,plex,jellyfin")]
DOWNLOAD_HINTS = [h.lower() for h in _list("DOWNLOAD_HINTS", "torrents,torrent,downloads,download,complete,incomplete,seeding,usenet,nzb")]
RECYCLE_HINTS = [h.lower() for h in _list("RECYCLE_HINTS", ".recycle,.trash,recycle,trash,.recyclebin,#recycle,@recycle,.trash-1000")]
RECYCLE_DAYS = int(os.environ.get("RECYCLE_DAYS", "30"))
MIN_SIZE_MB = int(os.environ.get("MIN_SIZE_MB", "50"))
HASH_BUDGET_MB = int(os.environ.get("HASH_BUDGET_MB", "20000"))   # full checksums per scan; beyond it duplicates are called "very likely" from the samples
SCAN_TIME = os.environ.get("SCAN_TIME", "04:30")
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".wmv", ".webm", ".mpg", ".mpeg", ".flv", ".iso"}
SAMPLE = 1024 * 1024
LOG = []
def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"; LOG.append(line); del LOG[:-400]; print(line, flush=True)

# ----------------------------------------------------------------------------------------------------------------------- db
def db():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False); c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS file(path TEXT PRIMARY KEY, dev INTEGER, ino INTEGER, nlink INTEGER, size INTEGER, mtime REAL, kind TEXT, seen REAL);
    CREATE TABLE IF NOT EXISTS fingerprint(dev INTEGER, ino INTEGER, size INTEGER, mtime REAL, sample TEXT, full TEXT, PRIMARY KEY(dev, ino, size, mtime));
    CREATE TABLE IF NOT EXISTS verdict(path TEXT PRIMARY KEY, code TEXT, title TEXT, kind TEXT, size INTEGER, wasted INTEGER, twin TEXT, evidence TEXT,
        first_seen REAL, last_seen REAL);
    CREATE TABLE IF NOT EXISTS run(id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, finished REAL, files INTEGER, bytes INTEGER, wasted INTEGER, note TEXT);
    CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS ignored(path TEXT PRIMARY KEY, at REAL);
    """); return c

def kv_get(c, k, d=None):
    r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone(); return r[0] if r else d
def kv_set(c, k, v): c.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, v)); c.commit()

# -------------------------------------------------------------------------------------------------------------- classify
def classify(path):
    """library | download | recycle | other, from explicit prefixes first, then folder-name hints (recycle wins over the rest)."""
    low = path.lower()
    for kind, dirs in (("recycle", RECYCLE_DIRS), ("download", DOWNLOAD_DIRS), ("library", LIBRARY_DIRS)):
        if any(low.startswith(d.lower() + "/") or low == d.lower() for d in dirs): return kind
    parts = [p.lower() for p in path.split("/")[:-1]]
    if any(p in RECYCLE_HINTS for p in parts): return "recycle"
    if any(p in DOWNLOAD_HINTS for p in parts): return "download"
    if any(p in LIBRARY_HINTS for p in parts): return "library"
    return "other"

_QUALITY = re.compile(r"\b(480p|576p|720p|1080p|2160p|4k|uhd|web[- ]?dl|webrip|bluray|blu-ray|bdrip|remux|hdtv|dvdrip|x264|x265|h\.?264|h\.?265|hevc|av1|hdr10?\+?|dv|dolby|atmos|dts|aac|ac3|ddp?5?\.?1|amzn|nf|dsnp|hmax|atvp|proper|repack|complete|multi|dual)\b.*$", re.I)
def friendly_title(path):
    """'Dune.Part.Two.2024.2160p.UHD.BluRay.x265-GRP.mkv' -> 'Dune Part Two 2024'; keeps SxxEyy."""
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"[._]+", " ", name).strip()
    name = _QUALITY.sub("", name).strip(" -")
    name = re.sub(r"\s*-\s*$", "", name)
    return re.sub(r"\s{2,}", " ", name) or os.path.basename(path)

def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1000 or unit == "PB": return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1000

# ------------------------------------------------------------------------------------------------------------ fingerprint
def sample_hash(path, size):
    """blake2b over the first, middle and last megabyte plus the size: cheap, catches almost every non-duplicate."""
    h = hashlib.blake2b(digest_size=16); h.update(str(size).encode())
    with open(path, "rb", buffering=0) as f:
        for off in sorted({0, max(0, size // 2 - SAMPLE // 2), max(0, size - SAMPLE)}):
            f.seek(off); h.update(f.read(SAMPLE))
    return h.hexdigest()

def full_hash(path):
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb", buffering=0) as f:
        while True:
            b = f.read(8 * 1024 * 1024)
            if not b: break
            h.update(b)
    return h.hexdigest()

def fingerprint(c, row, want_full, budget):
    """Returns (sample, full_or_None, budget_left). Cached by (dev, ino, size, mtime), so a file is hashed once in its life."""
    key = (row["dev"], row["ino"], row["size"], row["mtime"])
    r = c.execute("SELECT sample, full FROM fingerprint WHERE dev=? AND ino=? AND size=? AND mtime=?", key).fetchone()
    sample, full = (r["sample"], r["full"]) if r else (None, None)
    try:
        if sample is None: sample = sample_hash(row["path"], row["size"])
        if want_full and full is None and row["size"] <= budget: full = full_hash(row["path"]); budget -= row["size"]
    except OSError as e:
        log(f"could not read {row['path']}: {e}"); return None, None, budget
    c.execute("INSERT OR REPLACE INTO fingerprint(dev,ino,size,mtime,sample,full) VALUES(?,?,?,?,?,?)", key + (sample, full))
    return sample, full, budget

# ------------------------------------------------------------------------------------------------------------------- scan
SCANNING = {"on": False, "started": 0, "phase": "", "files": 0}
def walk(roots):
    for root in roots:
        if not os.path.isdir(root): log(f"scan root is not a folder: {root}"); continue
        stack = [root]
        while stack:
            d = stack.pop()
            try: it = os.scandir(d)
            except OSError as e: log(f"cannot list {d}: {e}"); continue
            with it:
                for e in it:
                    try:
                        if e.is_symlink(): continue
                        if e.is_dir(follow_symlinks=False): stack.append(e.path); continue
                        if not e.is_file(follow_symlinks=False): continue
                        if os.path.splitext(e.name)[1].lower() not in VIDEO_EXT: continue
                        st = e.stat(follow_symlinks=False)
                        if st.st_size < MIN_SIZE_MB * 1024 * 1024: continue
                        yield e.path, st
                    except OSError as e2: log(f"cannot stat {e.path}: {e2}")

def scan(c=None, now=None):
    """One pass: record every video file, group names that share an inode, find identical copies, judge recycle bins."""
    own = c is None; c = c or db(); now = now or time.time()
    if SCANNING["on"]: return None
    SCANNING.update(on=True, started=now, phase="listing files", files=0)
    try:
        started = now; n = 0; total = 0
        c.execute("DELETE FROM file")
        for path, st in walk(SCAN_ROOTS):
            c.execute("INSERT OR REPLACE INTO file(path,dev,ino,nlink,size,mtime,kind,seen) VALUES(?,?,?,?,?,?,?,?)",
                      (path, st.st_dev, st.st_ino, st.st_nlink, st.st_size, st.st_mtime, classify(path), now))
            n += 1; total += st.st_size; SCANNING["files"] = n
            if n % 500 == 0: c.commit()
        c.commit(); SCANNING["phase"] = "judging"
        wasted = judge(c, now)
        c.execute("INSERT INTO run(started,finished,files,bytes,wasted,note) VALUES(?,?,?,?,?,?)", (started, time.time(), n, total, wasted, "ok"))
        c.commit(); log(f"scan done: {n} files, {human(total)}, {human(wasted)} that can come back")
        return {"files": n, "bytes": total, "wasted": wasted}
    except Exception as e:
        log("scan failed: " + "".join(traceback.format_exception_only(type(e), e)).strip())
        c.execute("INSERT INTO run(started,finished,files,bytes,wasted,note) VALUES(?,?,?,?,?,?)", (now, time.time(), 0, 0, 0, "failed: " + str(e)[:200])); c.commit()
        raise
    finally:
        SCANNING.update(on=False, phase="")
        if own: c.close()

def judge(c, now):
    rows = [dict(r) for r in c.execute("SELECT * FROM file")]
    groups = {}
    for r in rows: groups.setdefault((r["dev"], r["ino"]), []).append(r)
    verdicts = {}   # path -> (code, kind, size, wasted, twin, evidence)
    lib_by_size = {}
    for r in rows:
        if r["kind"] == "library": lib_by_size.setdefault(r["size"], []).append(r)
    # 1. groups of names that share one inode
    for (dev, ino), members in groups.items():
        kinds = {m["kind"] for m in members}
        if "library" in kinds and "download" in kinds:
            lib = next(m for m in members if m["kind"] == "library")
            ev = f"inode {ino}, link count {lib['nlink']}, {len(members)} names on this scan"
            for m in members:
                if m is lib: verdicts[m["path"]] = ("REAL_COPY", m["kind"], m["size"], 0, [x["path"] for x in members if x is not lib][0], ev)
                elif m["kind"] == "download": verdicts[m["path"]] = ("LINKED_SEED", m["kind"], m["size"], 0, lib["path"], ev)
        elif kinds == {"library"} and len(members) > 1:
            for m in members: verdicts[m["path"]] = ("LIBRARY_TWIN", "library", m["size"], 0, [x["path"] for x in members if x is not m][0], f"inode {ino}, link count {m['nlink']}")
    # 2. downloads not linked to the library: identical copy, lookalike or unmatched
    budget = HASH_BUDGET_MB * 1024 * 1024
    for r in rows:
        if r["path"] in verdicts or r["kind"] != "download": continue
        candidates = [l for l in lib_by_size.get(r["size"], []) if (l["dev"], l["ino"]) != (r["dev"], r["ino"])]
        code, twin, ev = "DOWNLOAD_UNMATCHED", None, f"inode {r['ino']}, link count {r['nlink']}, no library file of {human(r['size'])}"
        if candidates:
            s1, f1, budget = fingerprint(c, r, True, budget)
            for l in candidates:
                s2, f2, budget = fingerprint(c, l, True, budget)
                if s1 is None or s2 is None: continue
                if s1 == s2 and (f1 is None or f2 is None or f1 == f2):
                    how = "checksums identical" if f1 and f2 else "samples identical (full checksum skipped: over this scan's hashing budget)"
                    code, twin, ev = "DUPLICATE_COPY", l["path"], f"inodes {r['ino']} and {l['ino']}, link count {r['nlink']} and {l['nlink']}, same size {human(r['size'])}, {how}"
                    break
                code, twin, ev = "LOOKALIKE", l["path"], f"same size as {friendly_title(l['path'])} ({human(r['size'])}) but the content differs"
        verdicts[r["path"]] = (code, "download", r["size"], r["size"] if code in ("DUPLICATE_COPY", "DOWNLOAD_UNMATCHED") else 0, twin, ev)
    # 3. everything else
    for r in rows:
        if r["path"] in verdicts: continue
        if r["kind"] == "library": verdicts[r["path"]] = ("LIBRARY_ONLY", "library", r["size"], 0, None, f"inode {r['ino']}, link count {r['nlink']}")
        elif r["kind"] == "recycle":
            days = int((now - r["mtime"]) // 86400)
            old = days > RECYCLE_DAYS
            verdicts[r["path"]] = ("BIN_OLD" if old else "BIN_RECENT", "recycle", r["size"], r["size"] if old else 0, None, f"in the bin for {days} days, rule is {RECYCLE_DAYS}")
        else: verdicts[r["path"]] = ("OTHER", "other", r["size"], 0, None, "folder name gives no hint whether this is library, downloads or a recycle bin; add it to LIBRARY_DIRS, DOWNLOAD_DIRS or RECYCLE_DIRS")
    # write back, keeping first_seen
    seen = set(verdicts)
    for path, (code, kind, size, wasted, twin, ev) in verdicts.items():
        c.execute("""INSERT INTO verdict(path,code,title,kind,size,wasted,twin,evidence,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(path) DO UPDATE SET code=excluded.code,title=excluded.title,kind=excluded.kind,size=excluded.size,wasted=excluded.wasted,
                     twin=excluded.twin,evidence=excluded.evidence,last_seen=excluded.last_seen""",
                  (path, code, friendly_title(path), kind, size, wasted, twin, ev, now, now))
    for r in c.execute("SELECT path FROM verdict").fetchall():
        if r["path"] not in seen: c.execute("DELETE FROM verdict WHERE path=?", (r["path"],))
    c.commit()
    return sum(v[3] for v in verdicts.values())

# ---------------------------------------------------------------------------------------------------------- plain words
LABEL = {"REAL_COPY": "Real copy", "LIBRARY_ONLY": "Only copy", "LIBRARY_TWIN": "Two names, one file", "LINKED_SEED": "Seeding name",
         "DUPLICATE_COPY": "Stored twice", "LOOKALIKE": "Looks alike", "DOWNLOAD_UNMATCHED": "Download without a match",
         "BIN_OLD": "Old recycle-bin file", "BIN_RECENT": "In the recycle bin", "OTHER": "Unsorted folder"}
NEEDS_YOU = {"DUPLICATE_COPY", "DOWNLOAD_UNMATCHED", "BIN_OLD", "OTHER"}
def sentence(v):
    code, size, twin = v["code"], human(v["size"]), v.get("twin")
    t = friendly_title(twin) if twin else ""
    if code == "REAL_COPY": return "One file with two names: the library and the download folder share it, so seeding costs no extra space."
    if code == "LINKED_SEED": return f"This is the download-folder name of the library file {t}. Same file, no extra space."
    if code == "LIBRARY_ONLY": return "One copy, in the library. Nothing to do."
    if code == "LIBRARY_TWIN": return f"Two names inside the library share one file ({t} is the other). No space wasted."
    if code == "DUPLICATE_COPY": return f"Stored twice: the library file {t} and this download are two separate files with identical content. Making them one file again would free {size}."
    if code == "LOOKALIKE": return f"Same size as {t} but different content, so not a duplicate. Worth a look if you expected one copy."
    if code == "DOWNLOAD_UNMATCHED": return f"Nothing in the library matches this download. It may be a leftover from an upgrade, an orphan, or something still seeding; the download-client check (phase 2) will tell. {size} to decide on."
    if code == "BIN_OLD": return f"In a recycle bin longer than your {RECYCLE_DAYS}-day rule. Emptying it would free {size}."
    if code == "BIN_RECENT": return f"In a recycle bin, within your {RECYCLE_DAYS}-day rule. It will show up here when the rule runs out."
    return "Not inside a folder I recognise as library, downloads or a recycle bin. Name the folder in the settings and it will be judged on the next scan."

def state(c):
    items = []
    ignored = {r["path"] for r in c.execute("SELECT path FROM ignored")}
    for r in c.execute("SELECT * FROM verdict ORDER BY wasted DESC, size DESC, path"):
        v = dict(r); v["label"] = LABEL.get(v["code"], v["code"]); v["plain"] = sentence(v); v["size_h"] = human(v["size"]); v["wasted_h"] = human(v["wasted"])
        v["needs_you"] = v["code"] in NEEDS_YOU and v["path"] not in ignored; v["ignored"] = v["path"] in ignored
        v["twin_title"] = friendly_title(v["twin"]) if v["twin"] else None
        items.append(v)
    counts, bytes_by = {}, {}
    for v in items:
        counts[v["code"]] = counts.get(v["code"], 0) + 1; bytes_by[v["code"]] = bytes_by.get(v["code"], 0) + v["wasted"]
    library_bytes = sum(r["size"] for r in c.execute("SELECT MAX(size) AS size FROM file WHERE kind='library' GROUP BY dev, ino"))
    disks = []
    for root in SCAN_ROOTS:
        try:
            u = shutil.disk_usage(root); disks.append({"root": root, "total": u.total, "used": u.used, "free": u.free, "total_h": human(u.total), "used_h": human(u.used), "free_h": human(u.free)})
        except OSError: disks.append({"root": root, "missing": True})
    last = c.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone()
    reclaim = bytes_by.get("DUPLICATE_COPY", 0) + bytes_by.get("BIN_OLD", 0); decide = bytes_by.get("DOWNLOAD_UNMATCHED", 0)
    lead = (f"Your library is {human(library_bytes)}. {human(reclaim)} is wasted on second copies and old recycle-bin files"
            + (f", and another {human(decide)} of downloads has no match yet" if decide else "") + ".") if last else "No scan yet. Press Scan now, or wait for the night."
    return {"app": APP, "tagline": TAGLINE, "phase": PHASE, "version": VERSION, "revision": REVISION, "read_only": True,
            "roots": SCAN_ROOTS, "disks": disks, "scanning": {k: SCANNING[k] for k in ("on", "phase", "files", "started")},
            "last_run": dict(last) if last else None, "counts": counts, "wasted_by": bytes_by, "wasted_by_h": {k: human(b) for k, b in bytes_by.items()},
            "library_bytes": library_bytes, "library_h": human(library_bytes), "reclaim_bytes": reclaim, "reclaim_h": human(reclaim),
            "decide_bytes": decide, "decide_h": human(decide), "lead": lead, "labels": LABEL, "items": items, "log": LOG[-40:]}

def about(c):
    last = c.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone(); health = []
    if not last: health.append({"level": "warn", "text": "No scan has run yet."})
    elif last["note"] != "ok": health.append({"level": "warn", "text": f"The last scan failed: {last['note']}"})
    elif time.time() - last["finished"] > 2 * 86400: health.append({"level": "warn", "text": "The last scan is more than two days old."})
    else: health.append({"level": "ok", "text": f"Last scan {datetime.datetime.fromtimestamp(last['finished']):%Y-%m-%d %H:%M}, {last['files']} files in {int(last['finished'] - last['started'])} s."})
    for r in SCAN_ROOTS:
        if not os.path.isdir(r): health.append({"level": "warn", "text": f"Scan root {r} is not mounted or not a folder."})
    health.append({"level": "ok", "text": "Phase 1 is read-only: nothing is moved, linked or deleted."})
    return {"app": APP, "tagline": TAGLINE, "version": VERSION, "revision": REVISION, "phase": PHASE, "health": health,
            "settings": {"SCAN_ROOTS": SCAN_ROOTS, "LIBRARY_DIRS": LIBRARY_DIRS, "DOWNLOAD_DIRS": DOWNLOAD_DIRS, "RECYCLE_DIRS": RECYCLE_DIRS,
                         "RECYCLE_DAYS": RECYCLE_DAYS, "MIN_SIZE_MB": MIN_SIZE_MB, "HASH_BUDGET_MB": HASH_BUDGET_MB, "SCAN_TIME": SCAN_TIME},
            "runs": [dict(r) for r in c.execute("SELECT * FROM run ORDER BY id DESC LIMIT 30")], "log": LOG[-60:],
            "links": {"source": "https://github.com/chrizantoz/dedupearr", "auditarr": "https://github.com/chrizantoz/auditarr"}}

# ------------------------------------------------------------------------------------------------------------------- http
UI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, code, obj):
        b = json.dumps(obj, default=str).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try: return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError: return {}
    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/", "/index.html"):
                b = open(UI_PATH, "rb").read(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
            c = db()
            try:
                if p == "/api/state": return self._json(200, state(c))
                if p == "/api/about": return self._json(200, about(c))
                if p == "/health":
                    last = c.execute("SELECT finished, note FROM run ORDER BY id DESC LIMIT 1").fetchone()
                    ok = last and last["note"] == "ok" and time.time() - last["finished"] < 2 * 86400
                    return self._json(200 if ok or not last else 503, {"ok": bool(ok), "version": VERSION})
            finally: c.close()
            self._json(404, {"error": "not found"})
        except Exception as e:
            log("request failed: " + str(e)); self._json(500, {"error": str(e)})
    def do_POST(self):
        p = self.path.split("?")[0]; body = self._body()
        try:
            if p == "/api/scan":
                if SCANNING["on"]: return self._json(200, {"ok": True, "note": "a scan is already running"})
                threading.Thread(target=scan, daemon=True).start(); return self._json(200, {"ok": True, "note": "scan started; the page updates itself"})
            if p in ("/api/ignore", "/api/unignore"):
                path = str(body.get("path") or "")
                if not path: return self._json(400, {"error": "which file?"})
                c = db()
                try:
                    if p == "/api/ignore": c.execute("INSERT OR REPLACE INTO ignored(path, at) VALUES(?,?)", (path, time.time())); note = "hidden from Needs you; it stays in Everything"
                    else: c.execute("DELETE FROM ignored WHERE path=?", (path,)); note = "back in Needs you"
                    c.commit()
                finally: c.close()
                return self._json(200, {"ok": True, "note": note})
            self._json(404, {"error": "not found"})
        except Exception as e:
            log("request failed: " + str(e)); self._json(500, {"error": str(e)})

def serve(port=HTTP_PORT, block=True):
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start(); log(f"{APP} {VERSION} listening on :{port}")
    if block:
        try: t.join()
        except KeyboardInterrupt: pass
    return srv

def scheduler():
    """A scan when the day's SCAN_TIME comes round, and one at start if the last one is more than 20 hours old."""
    c = db(); last = c.execute("SELECT finished FROM run WHERE note='ok' ORDER BY id DESC LIMIT 1").fetchone(); c.close()
    if not last or time.time() - last["finished"] > 20 * 3600: threading.Thread(target=scan, daemon=True).start()
    hh, mm = (int(x) for x in SCAN_TIME.split(":"))
    while True:
        now = datetime.datetime.now(); nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now: nxt += datetime.timedelta(days=1)
        time.sleep(max(1, (nxt - now).total_seconds()))
        try: scan()
        except Exception: pass

def main():
    ap = argparse.ArgumentParser(description=f"{APP}: {TAGLINE}")
    ap.add_argument("--daemon", action="store_true", help="serve the page and scan on a schedule")
    ap.add_argument("--scan", action="store_true", help="scan once and print the summary")
    ap.add_argument("--json", action="store_true", help="print the full state as JSON after --scan")
    a = ap.parse_args()
    if a.scan:
        r = scan(); c = db(); s = state(c); c.close()
        if a.json: print(json.dumps(s, indent=1, default=str))
        else:
            print(s["lead"])
            for code, n in sorted(s["counts"].items(), key=lambda x: -x[1]): print(f"  {LABEL[code]:<26} {n:>6}   {s['wasted_by_h'][code]:>9} wasted")
        return 0 if r else 1
    if a.daemon:
        threading.Thread(target=scheduler, daemon=True).start(); serve(); return 0
    ap.print_help(); return 2

if __name__ == "__main__": sys.exit(main())
