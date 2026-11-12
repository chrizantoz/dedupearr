#!/usr/bin/env python3
"""Dedupearr: the truth about your storage.

For every video file under your media and download folders it says what the file IS - the real copy, a hardlink that costs
nothing, a second copy that wastes space, a download nothing in the library matches, or an old recycle-bin entry - and shows
the evidence (inode, link count, size, checksum) in one plain sentence.

Phase 1: scan and tell. Phase 2 (in this file too): ask the download clients (Deluge, NZBGet, qBittorrent) and the arrs' import history, so a
download becomes "leftover", "orphan", "seeding only", with the client's seeding state and whether your seeding rule lets it go now. Still read-only.
Phase 3: fixes behind a dry run, a holding bin and a 14-day undo.

Verdict codes (never shown raw; see LABEL and sentence()):
  REAL_COPY          library file and download file are one file with two names (hardlink)       wasted 0
  LIBRARY_ONLY       one copy, in the library                                                     wasted 0
  LIBRARY_TWIN       two names inside the library share one file                                  wasted 0
  LINKED_SEED        the download-folder name of a REAL_COPY                                      wasted 0 (hidden under its library row)
  DUPLICATE_COPY     library file and download file are two files with identical content          wasted size
  DUPLICATE_HEADER   same, except the first megabyte: a header edited in place on one copy (tag)   wasted size
  LOOKALIKE          same size as a library file, different content                               wasted 0
  DOWNLOAD_UNMATCHED no library file matches this download (phase 2 decides what it is)           wasted size, "to decide"
  DOWNLOAD_OTHER_RELEASE  same, but the library holds a different release of the same title (a leftover from an upgrade, most likely)
  LEFTOVER           an arr imported this download and the library has since replaced or rewritten that file  wasted size
  ORPHAN             no client knows the file and no arr imported it                                wasted size
  SEEDING_ONLY       a client seeds it and no arr ever imported it (software, extras, kept torrents) wasted 0
Phase 3 (in progress): /api/fix shows the plan for a row (relink, remove through the client, holding bin, empty an old bin). FIX_MODE=dryrun by default:
nothing is touched; the real fixes arrive one at a time behind that switch, each with a holding bin and a 14-day undo.
  BIN_OLD            in a recycle bin longer than RECYCLE_DAYS                                     wasted size
  BIN_RECENT         in a recycle bin, within the rule                                            wasted 0
  OTHER              in a folder that is neither library, downloads nor recycle bin               wasted 0
"""
import os, re, sys, json, time, shutil, sqlite3, hashlib, threading, datetime, argparse, traceback, base64, urllib.request, urllib.parse, http.cookiejar
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
DOWNLOAD_HINTS = [h.lower() for h in _list("DOWNLOAD_HINTS", "torrents,torrent,downloads,download,complete,completed,incomplete,intermediate,seeding,seeds,usenet,nzb,nzbget,sabnzbd,qbittorrent,deluge")]
RECYCLE_HINTS = [h.lower() for h in _list("RECYCLE_HINTS", ".recycle,.trash,recycle,trash,.recyclebin,#recycle,@recycle,.trash-1000")]
RECYCLE_DAYS = int(os.environ.get("RECYCLE_DAYS", "30"))
MIN_SIZE_MB = int(os.environ.get("MIN_SIZE_MB", "50"))
HASH_BUDGET_MB = int(os.environ.get("HASH_BUDGET_MB", "20000"))   # full checksums per scan; beyond it duplicates are called "very likely" from the samples
SCAN_TIME = os.environ.get("SCAN_TIME", "04:30")
SANDBOX = os.environ.get("SANDBOX", "0") == "1"   # the public demo: a fake disk, anyone may click, resets on the hour; writes are rate-limited per visitor
SANDBOX_RATE = int(os.environ.get("SANDBOX_RATE", "60"))
SANDBOX_NOTE = "This is the public demo on a fake disk: every button works and it resets on the hour. Install Dedupearr to point it at your own library."
_RATE = {}
def sandbox_rate_ok(ip, limit=None):
    """At most `limit` writes per visitor per minute on the public demo."""
    limit = limit or SANDBOX_RATE; minute = int(time.time() // 60); key = (ip, minute)
    for k in [k for k in _RATE if k[1] < minute - 1]: _RATE.pop(k, None)
    _RATE[key] = _RATE.get(key, 0) + 1; return _RATE[key] <= limit
def _env(k, d=""): return os.environ.get(k, d).strip()
ARRS = {a: {"url": _env(f"{a.upper()}_URL").rstrip("/"), "key": _env(f"{a.upper()}_APIKEY")} for a in ("sonarr", "radarr", "whisparr", "sportarr", "lidarr")}   # every Sonarr/Radarr-shaped app with a v3 history
CLIENTS = {"Deluge": {"url": _env("DELUGE_URL").rstrip("/"), "password": _env("DELUGE_PASSWORD")},
           "NZBGet": {"url": _env("NZBGET_URL").rstrip("/"), "user": _env("NZBGET_USER"), "password": _env("NZBGET_PASSWORD")},
           "qBittorrent": {"url": _env("QBIT_URL").rstrip("/"), "user": _env("QBIT_USER"), "password": _env("QBIT_PASSWORD")}}
SEED_MIN_RATIO = float(_env("SEED_MIN_RATIO", "1.0") or 1.0); SEED_MIN_DAYS = float(_env("SEED_MIN_DAYS", "14") or 14)
SEED_RULE = _env("SEED_RULE", "both").lower()   # "both": ratio AND days must be reached (safe for private trackers, the default); "either": one of them is enough
CLIENT_PATH_MAP = [tuple(x.split("=", 1)) for x in _env("CLIENT_PATH_MAP").split(",") if "=" in x]   # clientpath=ourpath when a client sees the files under another path
CLIENT_STATUS = {}   # "Deluge" -> {"ok": bool, "n": int, "note": str}; shown on About

def map_client_path(p):
    for a, b in CLIENT_PATH_MAP:
        if p.startswith(a): return b.rstrip("/") + p[len(a.rstrip("/")):]
    return p

CLIENT_TIMEOUT = int(_env("CLIENT_TIMEOUT", "60") or 60)
def http_json(url, data=None, headers=None, cj=None, timeout=None, form=False):
    timeout = timeout or CLIENT_TIMEOUT
    """GET (data None) or POST JSON / form; returns the parsed JSON ({} for an empty body)."""
    body = (urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
    h = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=h)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)) if cj is not None else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r: raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}

def read_deluge():
    cfg = CLIENTS["Deluge"]
    if not cfg["url"]: return {}
    cj = http.cookiejar.CookieJar(); u = cfg["url"] + "/json"; n = [0]
    def call(m, params):
        n[0] += 1; r = http_json(u, {"method": m, "params": params, "id": n[0]}, cj=cj)
        if r.get("error"): raise RuntimeError(f"{m}: {r['error'].get('message') if isinstance(r['error'], dict) else r['error']}")
        return r.get("result")
    if not call("auth.login", [cfg["password"]]): raise RuntimeError("the web password was refused")
    if not call("web.connected", []):
        hosts = call("web.get_hosts", []) or []
        if hosts: call("web.connect", [hosts[0][0]])
    fields = ["name", "save_path", "state", "ratio", "seeding_time", "time_added", "label", "total_size", "is_finished", "progress", "files"]
    ts = call("core.get_torrents_status", [{}, fields]) or {}
    out = {}
    for tid, t in ts.items():
        for f in t.get("files") or []:
            out[map_client_path(os.path.join(t["save_path"], f["path"]))] = {"client": "Deluge", "name": t["name"], "state": str(t.get("state", "")).lower(), "ratio": float(t.get("ratio") or 0),
                                                                            "seeding_days": float(t.get("seeding_time") or 0) / 86400, "label": t.get("label") or "", "finished": bool(t.get("is_finished")), "id": tid}
    CLIENT_STATUS["Deluge"] = {"ok": True, "n": len(ts), "note": f"{len(ts)} torrents"}; return out

def read_nzbget():
    cfg = CLIENTS["NZBGet"]
    if not cfg["url"]: return {}
    H = {"Authorization": "Basic " + base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()}; u = cfg["url"] + "/jsonrpc"
    hist = http_json(u, {"method": "history", "params": [False]}, H).get("result") or []
    groups = http_json(u, {"method": "listgroups", "params": [0]}, H).get("result") or []
    out = {}
    for h in hist:   # a usenet download is a folder; any file under it belongs to it (keys end with '/')
        d = map_client_path(h.get("FinalDir") or h.get("DestDir") or "")
        if d: out[d.rstrip("/") + "/"] = {"client": "NZBGet", "name": h.get("Name", ""), "state": "finished" if str(h.get("Status", "")).startswith("SUCCESS") else "failed" if "FAILURE" in str(h.get("Status", "")) else str(h.get("Status", "")).lower(),
                                           "ratio": 0.0, "seeding_days": 0.0, "label": h.get("Category") or "", "finished": True, "id": h.get("NZBID")}
    for g in groups:
        d = map_client_path(g.get("DestDir") or "")
        if d: out[d.rstrip("/") + "/"] = {"client": "NZBGet", "name": g.get("NZBName", ""), "state": "downloading", "ratio": 0.0, "seeding_days": 0.0, "label": g.get("Category") or "", "finished": False, "id": g.get("NZBID")}
    CLIENT_STATUS["NZBGet"] = {"ok": True, "n": len(hist), "note": f"{len(hist)} in history, {len(groups)} downloading"}; return out

def read_qbit():
    cfg = CLIENTS["qBittorrent"]
    if not cfg["url"]: return {}
    cj = http.cookiejar.CookieJar(); base = cfg["url"] + "/api/v2"
    req = urllib.request.Request(base + "/auth/login", data=urllib.parse.urlencode({"username": cfg["user"], "password": cfg["password"]}).encode(), headers={"Referer": cfg["url"]})
    if "Ok" not in urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)).open(req, timeout=30).read().decode(): raise RuntimeError("the login was refused")
    out = {}; ts = http_json(base + "/torrents/info", cj=cj) or []
    for t in ts:
        for f in http_json(base + "/torrents/files?hash=" + t["hash"], cj=cj) or []:
            out[map_client_path(os.path.join(t["save_path"], f["name"]))] = {"client": "qBittorrent", "name": t["name"], "state": str(t.get("state", "")).lower(), "ratio": float(t.get("ratio") or 0),
                                                                             "seeding_days": float(t.get("seeding_time") or 0) / 86400, "label": t.get("category") or "", "finished": float(t.get("progress") or 0) >= 1, "id": t["hash"]}
    CLIENT_STATUS["qBittorrent"] = {"ok": True, "n": len(ts), "note": f"{len(ts)} torrents"}; return out

def read_arr_imports():
    """What each arr imported from where: dropped path (the download) -> imported path (the library file), newest first."""
    out = {}
    for app, cfg in ARRS.items():
        if not cfg["url"] or not cfg["key"]: continue
        H = {"X-Api-Key": cfg["key"]}; n = 0; page = 1
        try:
            while page <= 40:
                r = http_json(f"{cfg['url']}/api/v3/history?page={page}&pageSize=500&eventType=3&sortKey=date&sortDirection=descending", headers=H)
                recs = r.get("records") or []
                for rec in recs:
                    d = rec.get("data") or {}; dropped = map_client_path(d.get("droppedPath") or "")
                    if dropped and dropped not in out: out[dropped] = {"app": app, "imported": d.get("importedPath") or "", "when": str(rec.get("date", ""))[:10], "title": rec.get("sourceTitle", "")}
                n += len(recs)
                if len(recs) < 500: break
                page += 1
            CLIENT_STATUS[app.capitalize()] = {"ok": True, "n": n, "note": f"{n} imports in its history"}
        except Exception as e: CLIENT_STATUS[app.capitalize()] = {"ok": False, "n": 0, "note": str(e)[:160]}; note_error(f"{app}: {e}")
    return out

def gather_context():
    """Ask every configured client and arr once per scan. A client that fails is reported on About and simply leaves its files unexplained."""
    clients = {}
    for name, fn in (("Deluge", read_deluge), ("NZBGet", read_nzbget), ("qBittorrent", read_qbit)):
        if not CLIENTS[name]["url"]: CLIENT_STATUS.pop(name, None); continue
        try: clients.update(fn())
        except Exception as e: CLIENT_STATUS[name] = {"ok": False, "n": 0, "note": str(e)[:160]}; note_error(f"{name}: {e}")
    return {"clients": clients, "imports": read_arr_imports(), "clients_ok": any(v["ok"] for k, v in CLIENT_STATUS.items() if k in CLIENTS)}

def client_for(path, clients):
    if path in clients: return clients[path]
    d = path
    while len(d) > 1:
        d = os.path.dirname(d)
        if d + "/" in clients: return clients[d + "/"]
    return None

def seeding_done(ci):
    """Nothing holds the file (no client knows it), or the client finished it and your seeding rule is met."""
    if ci is None: return True
    if not ci.get("finished"): return False
    if ci.get("client") == "NZBGet": return True
    r_ok, d_ok = ci.get("ratio", 0) >= SEED_MIN_RATIO, ci.get("seeding_days", 0) >= SEED_MIN_DAYS
    return (r_ok or d_ok) if SEED_RULE == "either" else (r_ok and d_ok)

def client_text(ci):
    if ci is None: return "not in any download client"
    if ci["client"] == "NZBGet": return f"NZBGet: {ci['state']}" + (f" ({ci['label']})" if ci.get("label") else "")
    return f"{ci['client']}: {ci['state']}, ratio {ci['ratio']:.2f}, {ci['seeding_days']:.0f} days" + (f", label {ci['label']}" if ci.get("label") else "")

FIX_MODE = (_env("FIX_MODE", "dryrun") or "dryrun").lower()   # off | dryrun | on. Phase 3: every fix shows its steps first; "on" arrives step by step and stays off until you turn it on
HOLDING_DIR = _env("HOLDING_DIR") or (SCAN_ROOTS[0].rstrip("/") + "/.dedupearr-holding"); HOLD_DAYS = int(_env("HOLD_DAYS", "14") or 14)
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".wmv", ".webm", ".mpg", ".mpeg", ".flv", ".iso"}
SAMPLE = 1024 * 1024
LOG = []; ERRORS = []   # ERRORS: what went wrong, for the About page ("Recent errors") and the health line
def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"; LOG.append(line); del LOG[:-400]; print(line, flush=True)
def note_error(msg):
    ERRORS.append({"at": time.time(), "text": str(msg)[:300]}); del ERRORS[:-200]; log(msg)
def errors_recent(hours=24):
    cut = time.time() - hours * 3600; return [e for e in ERRORS if e["at"] >= cut]

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
    CREATE TABLE IF NOT EXISTS action(ts REAL, mode TEXT, kind TEXT, path TEXT, detail TEXT, result TEXT, undo TEXT);
    """)
    cols = {r[1] for r in c.execute("PRAGMA table_info(verdict)")}
    for col, typ in (("client", "TEXT"), ("removable", "INTEGER")):
        if col not in cols: c.execute(f"ALTER TABLE verdict ADD COLUMN {col} {typ}")
    return c

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
    name = re.sub(r"\{[^}]*\}|\[[^\]]*\]", " ", name)   # arr-style tags: {tmdb-123}, {edition-…}, [Bluray-1080p][x264]
    name = re.sub(r"[._]+", " ", name).strip()
    name = " ".join(_QUALITY.sub("", name).split())
    m = re.search(r" - ?-?[A-Za-z0-9]{2,12}$", name)   # a trailing release group after a dash: '- -playHD', '- GRP' (no nested repeats: CodeQL py/redos)
    if m: name = name[:m.start()]
    name = name.rstrip(" -")
    name = re.sub(r"\s*-\s*$", "", name)
    return re.sub(r"\s{2,}", " ", name) or os.path.basename(path)

def title_key(path):
    """A loose identity for 'same title': normalised words before the year (movies) or before SxxEyy (episodes), plus that year or episode.
    'Scary.Movie.2026.1080p.BluRay.x264-EniaHD.mkv' and 'Scary Movie (2026) {tmdb-1273221} - {edition-Theatrical Cut} [Bluray-1080p]-playHD.mkv' -> 'scary movie 2026'."""
    name = os.path.splitext(os.path.basename(path))[0]
    norm = lambda t: " ".join(re.sub(r"\b(19|20)\d\d\b", " ", re.sub(r"[^a-z0-9]+", " ", t.lower())).split())   # the year is dropped from an episode's title part: 'Bleach (2004) - S17E46' and 'Bleach.S17E46' are one show
    m = re.search(r"\b(s\d{1,2})[ ._-]?(e\d{1,3})\b", name, re.I)
    if m: return norm(name[:m.start()]) + " " + m.group(1).lower() + m.group(2).lower()
    m = re.search(r"(?<![a-z])(e\d{2,3})\b", name, re.I)   # anime style '- E04'
    if m: return norm(name[:m.start()]) + " e?" + m.group(1).lower()[1:]
    m = re.search(r"\b(19\d\d|20\d\d)\b", name)
    if m and m.start() > 0: return norm(name[:m.start()]) + " " + m.group(1)   # a movie keeps its year: Heat 1995 is not Heat 2024
    return None

def same_title(a, b):
    if not a or not b: return False
    if a == b: return True
    ta, ea = a.rsplit(" ", 1); tb, eb = b.rsplit(" ", 1)
    if ta != tb: return False
    return ea.startswith("e?") and eb.endswith(ea[2:]) or eb.startswith("e?") and ea.endswith(eb[2:])   # 'E04' without a season matches S01E04

def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1000 or unit == "PB": return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1000

# ------------------------------------------------------------------------------------------------------------ fingerprint
def sample_hash(path, size):
    """Three blake2b hashes, 'first:middle:last' megabyte: cheap, catches almost every non-duplicate, and tells a file whose
    header was edited in place (a language tag, a title) from a file with different content."""
    out = []
    with open(path, "rb", buffering=0) as f:
        for off in (0, max(0, size // 2 - SAMPLE // 2), max(0, size - SAMPLE)):
            f.seek(off); out.append(hashlib.blake2b(f.read(SAMPLE), digest_size=12).hexdigest())
    return ":".join(out)

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
        note_error(f"could not read {row['path']}: {e}"); return None, None, budget
    c.execute("INSERT OR REPLACE INTO fingerprint(dev,ino,size,mtime,sample,full) VALUES(?,?,?,?,?,?)", key + (sample, full))
    return sample, full, budget

# ------------------------------------------------------------------------------------------------------------------- scan
SCANNING = {"on": False, "started": 0, "phase": "", "files": 0}
def walk(roots):
    for root in roots:
        if not os.path.isdir(root): note_error(f"scan root is not a folder: {root}"); continue
        stack = [root]
        while stack:
            d = stack.pop()
            try: it = os.scandir(d)
            except OSError as e: note_error(f"cannot list {d}: {e}"); continue
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
                    except OSError as e2: note_error(f"cannot stat {e.path}: {e2}")

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
        c.commit(); SCANNING["phase"] = "asking the download clients and the arrs"
        ctx = gather_context(); SCANNING["phase"] = "judging"
        wasted = judge(c, now, ctx)
        c.execute("INSERT INTO run(started,finished,files,bytes,wasted,note) VALUES(?,?,?,?,?,?)", (started, time.time(), n, total, wasted, "ok"))
        c.commit(); log(f"scan done: {n} files, {human(total)}, {human(wasted)} that can come back")
        return {"files": n, "bytes": total, "wasted": wasted}
    except Exception as e:
        note_error("scan failed: " + "".join(traceback.format_exception_only(type(e), e)).strip())
        c.execute("INSERT INTO run(started,finished,files,bytes,wasted,note) VALUES(?,?,?,?,?,?)", (now, time.time(), 0, 0, 0, "failed: " + str(e)[:200])); c.commit()
        raise
    finally:
        SCANNING.update(on=False, phase="")
        if own: c.close()

def judge(c, now, ctx=None):
    ctx = ctx or {"clients": {}, "imports": {}, "clients_ok": False}; clients, imports, clients_ok = ctx["clients"], ctx["imports"], ctx.get("clients_ok", False)
    extra = {}   # path -> (client info or None, removable)
    rows = [dict(r) for r in c.execute("SELECT * FROM file")]
    groups = {}
    for r in rows: groups.setdefault((r["dev"], r["ino"]), []).append(r)
    verdicts = {}   # path -> (code, kind, size, wasted, twin, evidence)
    lib_by_size = {}; lib_by_title = {}
    for r in rows:
        if r["kind"] == "library":
            lib_by_size.setdefault(r["size"], []).append(r)
            k = title_key(r["path"])
            if k: lib_by_title.setdefault(k.rsplit(" ", 1)[0], []).append((k, r))
    # 1. groups of names that share one inode
    for (dev, ino), members in groups.items():
        kinds = {m["kind"] for m in members}
        if "library" in kinds and "download" in kinds:
            lib = next(m for m in members if m["kind"] == "library")
            ev = f"inode {ino}, link count {lib['nlink']}, {len(members)} names on this scan"
            for m in members:
                ci = client_for(m["path"], clients) if m["kind"] == "download" else None
                if ci: extra[m["path"]] = (ci, False)
                if m is lib: verdicts[m["path"]] = ("REAL_COPY", m["kind"], m["size"], 0, [x["path"] for x in members if x is not lib][0], ev)
                elif m["kind"] == "download": verdicts[m["path"]] = ("LINKED_SEED", m["kind"], m["size"], 0, lib["path"], ev + ("; " + client_text(ci) if ci or clients_ok else ""))
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
                if r["size"] > 3 * SAMPLE and s1.split(":")[1:] == s2.split(":")[1:]:   # middle and end identical, only the first megabyte differs: a header edited in place
                    code, twin, ev = "DUPLICATE_HEADER", l["path"], f"inodes {r['ino']} and {l['ino']}, same size {human(r['size'])}, middle and end identical, only the first megabyte differs (a tag or title was edited on one copy)"
                    break
                code, twin, ev = "LOOKALIKE", l["path"], f"same size as {friendly_title(l['path'])} ({human(r['size'])}) but the content differs"
        if code == "DOWNLOAD_UNMATCHED":   # nothing identical, but is there a different release of the same title?
            k = title_key(r["path"]) or title_key(os.path.dirname(r["path"]) + ".x")
            for k2, l in lib_by_title.get((k or " ").rsplit(" ", 1)[0], []):
                if same_title(k, k2):
                    code, twin, ev = "DOWNLOAD_OTHER_RELEASE", l["path"], f"inode {r['ino']}, link count {r['nlink']}, {human(r['size'])}; the library has a different release of the same title ({human(l['size'])}, inode {l['ino']})"; break
        ci = client_for(r["path"], clients); ai = imports.get(r["path"])
        if code in ("DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LOOKALIKE"):   # a lookalike is only a size coincidence: the client and the arr say what the file really is
            if ai and ai.get("imported"):
                gone = not os.path.exists(ai["imported"])
                code, twin = "LEFTOVER", twin or ai["imported"]
                ev += f"; {ai['app'].capitalize()} imported it on {ai['when']} to {ai['imported']}" + (", and that file has since been replaced" if gone else ", which has since been rewritten (different content now)")
            elif clients_ok and ci is None and any(a["url"] for a in ARRS.values()): code = "ORPHAN"; ev += "; no download client knows this file and no arr imported it"
            elif ci and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE") and any(a["url"] for a in ARRS.values()): code = "SEEDING_ONLY"; ev += "; no arr ever imported it"
        if ci or clients_ok: ev += "; " + client_text(ci)
        extra[r["path"]] = (ci, seeding_done(ci))
        verdicts[r["path"]] = (code, "download", r["size"], r["size"] if code in ("DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN") else 0, twin, ev)
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
        ci, rem = extra.get(path, (None, True))
        c.execute("""INSERT INTO verdict(path,code,title,kind,size,wasted,twin,evidence,first_seen,last_seen,client,removable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(path) DO UPDATE SET code=excluded.code,title=excluded.title,kind=excluded.kind,size=excluded.size,wasted=excluded.wasted,
                     twin=excluded.twin,evidence=excluded.evidence,last_seen=excluded.last_seen,client=excluded.client,removable=excluded.removable""",
                  (path, code, friendly_title(path), kind, size, wasted, twin, ev, now, now, json.dumps(ci) if ci else None, 1 if rem else 0))
    for r in c.execute("SELECT path FROM verdict").fetchall():
        if r["path"] not in seen: c.execute("DELETE FROM verdict WHERE path=?", (r["path"],))
    c.commit()
    return sum(v[3] for v in verdicts.values())

# ---------------------------------------------------------------------------------------------------------- plain words
LABEL = {"REAL_COPY": "Real copy", "LIBRARY_ONLY": "Only copy", "LIBRARY_TWIN": "Two names, one file", "LINKED_SEED": "Seeding name",
         "DUPLICATE_COPY": "Stored twice", "DUPLICATE_HEADER": "Stored twice, header differs", "LOOKALIKE": "Looks alike", "DOWNLOAD_UNMATCHED": "Download without a match", "DOWNLOAD_OTHER_RELEASE": "Different release in library",
         "LEFTOVER": "Leftover from an upgrade", "ORPHAN": "Orphan", "SEEDING_ONLY": "Seeding only",
         "BIN_OLD": "Old recycle-bin file", "BIN_RECENT": "In the recycle bin", "OTHER": "Unsorted folder"}
NEEDS_YOU = {"DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN", "BIN_OLD", "OTHER"}
def seed_sentence(ci):
    if ci is None: return "Nothing holds it, so it can go now."
    if ci.get("client") == "NZBGet": return "It came from usenet, so there is no seeding to wait for; it can go now."
    if seeding_done(ci): return f"{ci['client']} has seeded it to ratio {ci['ratio']:.1f} over {ci['seeding_days']:.0f} days, past your rule, so it can go now."
    return f"{ci['client']} is still seeding it (ratio {ci['ratio']:.2f}, {ci['seeding_days']:.0f} days); your rule keeps it until ratio {SEED_MIN_RATIO:g} {'or' if SEED_RULE == 'either' else 'and'} {SEED_MIN_DAYS:g} days."
def sentence(v):
    code, size, twin = v["code"], human(v["size"]), v.get("twin")
    t = friendly_title(twin) if twin else ""
    ci = v.get("client") if isinstance(v.get("client"), dict) else (json.loads(v["client"]) if v.get("client") else None)
    tail = (" " + seed_sentence(ci)) if (ci or v.get("clients_known")) else ""
    if code == "LEFTOVER": return f"A leftover from an upgrade: this download was imported into the library and the library has since moved on ({t} is what it keeps now). {size} to get back.{tail}"
    if code == "ORPHAN": return f"An orphan: no download client knows this file and no arr imported it. {size} to get back. Nothing holds it, so it can go now."
    if code == "SEEDING_ONLY": return f"Kept for seeding: {ci['client'] if ci else 'your client'} is seeding it and nothing in the library came from it. Not counted as waste; it goes when you remove the torrent."
    if code == "REAL_COPY": return "One file with two names: the library and the download folder share it, so seeding costs no extra space."
    if code == "LINKED_SEED": return f"This is the download-folder name of the library file {t}. Same file, no extra space."
    if code == "LIBRARY_ONLY": return "One copy, in the library. Nothing to do."
    if code == "LIBRARY_TWIN": return f"Two names inside the library share one file ({t} is the other). No space wasted."
    if code == "DUPLICATE_COPY": return f"Stored twice: the library file {t} and this download are two separate files with identical content. Making them one file again would free {size}.{tail}"
    if code == "DUPLICATE_HEADER": return f"Stored twice: the library file {t} and this download are the same video, except the first part of the file differs. That usually means a tag or title was edited on one copy (Auditarr does this when it fixes audio languages). Relinking would not be safe, because the torrent would put the old header back; once seeding is done the download can go. {size} to get back.{tail}"
    if code == "LOOKALIKE": return f"Same size as {t} but different content, so not a duplicate. Worth a look if you expected one copy."
    if code == "DOWNLOAD_UNMATCHED": return f"Nothing in the library matches this download. It may be a leftover from an upgrade, an orphan, or something still seeding; the download-client check (phase 2) will tell. {size} to decide on."
    if code == "DOWNLOAD_OTHER_RELEASE": return f"Nothing identical in the library, but it holds a different release of the same title: {t}. If that one is the keeper, this download is a leftover from an upgrade and can go once seeding is done. {size} to get back.{tail}"
    if code == "BIN_OLD": return f"In a recycle bin longer than your {RECYCLE_DAYS}-day rule. Emptying it would free {size}."
    if code == "BIN_RECENT": return f"In a recycle bin, within your {RECYCLE_DAYS}-day rule. It will show up here when the rule runs out."
    return "Not inside a folder I recognise as library, downloads or a recycle bin. Name the folder in the settings and it will be judged on the next scan."

# -------------------------------------------------------------------------------------------------------------- fixes (phase 3)
FIXABLE = {"DUPLICATE_COPY": "relink", "DUPLICATE_HEADER": "remove", "DOWNLOAD_OTHER_RELEASE": "remove", "LEFTOVER": "remove", "ORPHAN": "bin", "BIN_OLD": "empty"}
def plan_fix(v):
    """The exact steps a fix would take for this file, in plain words, before anything is touched. Returns (kind, steps, frees) or (None, [reason], 0)."""
    code = v["code"]; ci = v.get("client") if isinstance(v.get("client"), dict) else (json.loads(v["client"]) if v.get("client") else None)
    kind = FIXABLE.get(code)
    if not kind: return None, ["Nothing to fix here: this file is fine or needs no change."], 0
    if not v.get("removable", True) and kind in ("remove", "relink"): return None, [seed_sentence(ci)], 0
    hold = f"{HOLDING_DIR}/{os.path.basename(v['path'])}"
    if kind == "relink": steps = ["Check both checksums again right before the change.", f"Move this copy into the holding bin at {hold} (nothing is deleted).", f"Create a hardlink from the library file {friendly_title(v['twin'] or '')} at this download's name, so the client keeps seeding the same bytes.",
                                 "Confirm the link count is now 2 and the client still sees the file; if anything fails, put the copy back.", f"Keep the moved copy for {HOLD_DAYS} days, then delete it, unless you press Undo."]
    elif kind == "remove": steps = ([f"Ask {ci['client']} to remove the torrent '{ci['name']}' without deleting its files." if ci and ci.get("client") != "NZBGet" else ("Remove the entry from NZBGet's history." if ci else "No client holds this file, so there is nothing to tell a client.")]
                                    + [f"Move the file into the holding bin at {hold} (nothing is deleted).", f"Keep it for {HOLD_DAYS} days, then delete it, unless you press Undo."])
    elif kind == "bin": steps = [f"Move the file into the holding bin at {hold} (nothing is deleted).", f"Keep it for {HOLD_DAYS} days, then delete it, unless you press Undo."]
    else: steps = [f"The file has sat in its recycle bin longer than your {RECYCLE_DAYS}-day rule.", "Delete it for good (it was already thrown away once; there is no second bin)."]
    return kind, steps, v.get("wasted") or 0

def record_action(c, mode, kind, path, detail, result, undo=None):
    c.execute("INSERT INTO action(ts,mode,kind,path,detail,result,undo) VALUES(?,?,?,?,?,?,?)", (time.time(), mode, kind, path, detail[:500], result[:500], json.dumps(undo) if undo else None)); c.commit()

def ui_fix(c, path):
    """POST /api/fix: show the plan (dry run) or carry it out (on). Phase 3 ships the 'on' branch fix by fix; until then every call is a dry run."""
    if SCANNING["on"]: return 409, {"error": "a scan is running; try again in a minute, so a fix never races the scan"}
    r = c.execute("SELECT * FROM verdict WHERE path=?", (path,)).fetchone()
    if not r: return 404, {"error": "that file is not in the last scan"}
    v = dict(r); v["removable"] = bool(v.get("removable"))
    kind, steps, frees = plan_fix(v)
    if not kind: return 400, {"error": steps[0]}
    if FIX_MODE == "off": return 403, {"error": "fixes are switched off (FIX_MODE=off); the plan is still shown on the row"}
    if FIX_MODE == "dryrun" or True:   # the 'on' branch lands with P3.1; a dry run is all that can happen today
        record_action(c, "dryrun", kind, path, " | ".join(steps), f"dry run: would free {human(frees)}")
        return 200, {"ok": True, "dry_run": True, "kind": kind, "steps": steps, "frees": frees, "frees_h": human(frees), "note": f"Dry run: nothing was changed. This fix would free {human(frees)} in {len(steps)} steps."}

def actions(c, limit=100):
    return [dict(r) | {"when": datetime.datetime.fromtimestamp(r["ts"]).isoformat(timespec="minutes")} for r in c.execute("SELECT * FROM action ORDER BY ts DESC LIMIT ?", (limit,))]

def state(c):
    items = []
    ignored = {r["path"] for r in c.execute("SELECT path FROM ignored")}
    for r in c.execute("SELECT * FROM verdict ORDER BY wasted DESC, size DESC, path"):
        v = dict(r); v["clients_known"] = bool(CLIENT_STATUS); v["label"] = LABEL.get(v["code"], v["code"]); v["plain"] = sentence(v); v["size_h"] = human(v["size"]); v["wasted_h"] = human(v["wasted"])
        v["needs_you"] = v["code"] in NEEDS_YOU and v["path"] not in ignored; v["ignored"] = v["path"] in ignored
        v["twin_title"] = friendly_title(v["twin"]) if v["twin"] else None
        ci = json.loads(v["client"]) if v.get("client") else None; v["client"] = ci; v["client_text"] = client_text(ci) if (ci or (v["kind"] == "download" and CLIENT_STATUS)) else ""
        v["removable"] = bool(v.get("removable")) and v["needs_you"]
        v["fixable"] = v["code"] in FIXABLE and v["needs_you"] and (v["code"] in ("ORPHAN", "BIN_OLD") or v["removable"])
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
    reclaim = sum(bytes_by.get(k, 0) for k in ("DUPLICATE_COPY", "DUPLICATE_HEADER", "BIN_OLD", "LEFTOVER", "ORPHAN")); decide = bytes_by.get("DOWNLOAD_UNMATCHED", 0) + bytes_by.get("DOWNLOAD_OTHER_RELEASE", 0)
    now_bytes = sum(v["wasted"] for v in items if v["removable"] and v["code"] != "DOWNLOAD_UNMATCHED")
    lead = (f"Your library is {human(library_bytes)}. {human(reclaim)} is wasted on second copies, leftovers, orphans and old recycle-bin files"
            + (f", and another {human(decide)} of downloads is still undecided" if decide else "") + "."
            + (f" {human(now_bytes)} of it can go right now by your seeding rule." if now_bytes and CLIENT_STATUS else "")) if last else "No scan yet. Press Scan now, or wait for the night."
    return {"app": APP, "tagline": TAGLINE, "phase": PHASE, "version": VERSION, "revision": REVISION, "read_only": True, "sandbox": SANDBOX, "sandbox_note": SANDBOX_NOTE if SANDBOX else "",
            "roots": SCAN_ROOTS, "disks": disks, "scanning": {k: SCANNING[k] for k in ("on", "phase", "files", "started")},
            "last_run": dict(last) if last else None, "counts": counts, "wasted_by": bytes_by, "wasted_by_h": {k: human(b) for k, b in bytes_by.items()},
            "library_bytes": library_bytes, "library_h": human(library_bytes), "reclaim_bytes": reclaim, "reclaim_h": human(reclaim),
            "decide_bytes": decide, "decide_h": human(decide), "now_bytes": now_bytes, "now_h": human(now_bytes), "lead": lead, "labels": LABEL, "items": items, "log": LOG[-40:],
            "clients": CLIENT_STATUS, "seed_rule": {"ratio": SEED_MIN_RATIO, "days": SEED_MIN_DAYS, "rule": SEED_RULE}, "fix_mode": FIX_MODE, "holding_dir": HOLDING_DIR}

def about(c):
    last = c.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone(); health = []
    if not last: health.append({"level": "warn", "text": "No scan has run yet."})
    elif last["note"] != "ok": health.append({"level": "warn", "text": f"The last scan failed: {last['note']}"})
    elif time.time() - last["finished"] > 2 * 86400: health.append({"level": "warn", "text": "The last scan is more than two days old."})
    else: health.append({"level": "ok", "text": f"Last scan {datetime.datetime.fromtimestamp(last['finished']):%Y-%m-%d %H:%M}, {last['files']} files in {int(last['finished'] - last['started'])} s."})
    for r in SCAN_ROOTS:
        if not os.path.isdir(r): health.append({"level": "warn", "text": f"Scan root {r} is not mounted or not a folder."})
    errs = errors_recent()
    health.append({"level": "warn", "text": f"{len(errs)} error{'s' if len(errs) != 1 else ''} in the last 24 hours; the newest: {errs[-1]['text'][:120]}"} if errs else {"level": "ok", "text": "No errors in the last 24 hours."})
    for name, st_ in sorted(CLIENT_STATUS.items()): health.append({"level": "ok" if st_["ok"] else "warn", "text": f"{name}: {st_['note']}" if st_["ok"] else f"{name} could not be read: {st_['note']}"})
    if not any(c["url"] for c in CLIENTS.values()) and not any(a["url"] for a in ARRS.values()): health.append({"level": "warn", "text": "No download client or arr is connected, so downloads without a match stay undecided. Set DELUGE_URL / NZBGET_URL / QBIT_URL and SONARR_URL / RADARR_URL (see the README)."})
    elif not CLIENT_STATUS and last: health.append({"level": "ok", "text": "Clients and arrs are configured; they are asked on the next scan."})
    health.append({"level": "ok", "text": {"off": "Fixes are switched off.", "dryrun": "Fixes run as a dry run: every 'Preview fix' shows the exact steps and nothing is moved, linked or deleted.", "on": "Fixes are on: files move to the holding bin first and can be undone for %d days." % HOLD_DAYS}.get(FIX_MODE, FIX_MODE)})
    if SANDBOX: health.append({"level": "ok", "text": SANDBOX_NOTE})
    return {"app": APP, "tagline": TAGLINE, "version": VERSION, "revision": REVISION, "phase": PHASE, "health": health,
            "settings": {"SCAN_ROOTS": SCAN_ROOTS, "LIBRARY_DIRS": LIBRARY_DIRS, "DOWNLOAD_DIRS": DOWNLOAD_DIRS, "RECYCLE_DIRS": RECYCLE_DIRS,
                         "RECYCLE_DAYS": RECYCLE_DAYS, "MIN_SIZE_MB": MIN_SIZE_MB, "HASH_BUDGET_MB": HASH_BUDGET_MB, "SCAN_TIME": SCAN_TIME,
                         "SEED_MIN_RATIO": SEED_MIN_RATIO, "SEED_MIN_DAYS": SEED_MIN_DAYS, "SEED_RULE": SEED_RULE, "CLIENT_PATH_MAP": CLIENT_PATH_MAP, "FIX_MODE": FIX_MODE, "HOLDING_DIR": HOLDING_DIR, "HOLD_DAYS": HOLD_DAYS,
                         "connections": {**{a: (c["url"] or "not set") for a, c in ARRS.items()}, **{n: (c["url"] or "not set") for n, c in CLIENTS.items()}}},
            "runs": [dict(r) for r in c.execute("SELECT * FROM run ORDER BY id DESC LIMIT 30")], "log": LOG[-60:], "errors": errors_recent()[-30:],
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
                if p == "/api/actions": return self._json(200, {"actions": actions(c), "fix_mode": FIX_MODE})
                if p == "/health":
                    last = c.execute("SELECT finished, note FROM run ORDER BY id DESC LIMIT 1").fetchone()
                    ok = last and last["note"] == "ok" and time.time() - last["finished"] < 2 * 86400
                    return self._json(200 if ok or not last else 503, {"ok": bool(ok), "version": VERSION})
            finally: c.close()
            self._json(404, {"error": "not found"})
        except Exception as e:
            note_error("request failed: " + str(e)); self._json(500, {"error": str(e)})
    def do_POST(self):
        p = self.path.split("?")[0]; body = self._body()
        try:
            if SANDBOX and not sandbox_rate_ok((self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()):
                return self._json(429, {"error": f"slow down: the demo allows {SANDBOX_RATE} clicks a minute per visitor"})
            if p == "/api/scan":
                if SCANNING["on"]: return self._json(200, {"ok": True, "note": "a scan is already running"})
                threading.Thread(target=scan, daemon=True).start(); return self._json(200, {"ok": True, "note": "scan started; the page updates itself"})
            if p == "/api/fix":
                path = str(body.get("path") or "")
                if not path: return self._json(400, {"error": "which file?"})
                c = db()
                try: code, obj = ui_fix(c, path)
                finally: c.close()
                return self._json(code, obj)
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
            note_error("request failed: " + str(e)); self._json(500, {"error": str(e)})

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
