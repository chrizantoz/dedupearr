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
  DOWNLOADING        a client is still downloading it                                              wasted 0
  AWAITING_IMPORT    finished, and an arr has it in its queue waiting to import (or blocked)        wasted 0
Phase 3 (in progress): /api/fix shows the plan for a row (relink, remove through the client, holding bin, empty an old bin). FIX_MODE=dryrun by default:
nothing is touched. With FIX_MODE=on the fixes in LIVE_KINDS run (P3.1: orphans move to the holding bin); /api/restore puts a held file back for HOLD_DAYS.
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
SEED_RULE = _env("SEED_RULE", "auto").lower()   # "auto" (default): private torrents need ratio AND days, public ones either; "both" / "either" for every torrent
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

def deluge_session():
    """A signed-in call(method, params) for Deluge's web JSON."""
    cfg = CLIENTS["Deluge"]; cj = http.cookiejar.CookieJar(); u = cfg["url"] + "/json"; n = [0]
    def call(m, params):
        n[0] += 1; r = http_json(u, {"method": m, "params": params, "id": n[0]}, cj=cj)
        if r.get("error"): raise RuntimeError(f"{m}: {r['error'].get('message') if isinstance(r['error'], dict) else r['error']}")
        return r.get("result")
    if not call("auth.login", [cfg["password"]]): raise RuntimeError("the web password was refused")
    if not call("web.connected", []):
        hosts = call("web.get_hosts", []) or []
        if hosts: call("web.connect", [hosts[0][0]])
    return call
def read_deluge():
    if not CLIENTS["Deluge"]["url"]: return {}
    call = deluge_session()
    fields = ["name", "save_path", "state", "ratio", "seeding_time", "time_added", "label", "total_size", "is_finished", "progress", "files", "private"]
    ts = call("core.get_torrents_status", [{}, fields]) or {}
    out = {}
    for tid, t in ts.items():
        for f in t.get("files") or []:
            out[map_client_path(os.path.join(t["save_path"], f["path"]))] = {"client": "Deluge", "name": t["name"], "state": str(t.get("state", "")).lower(), "ratio": float(t.get("ratio") or 0),
                                                                            "seeding_days": float(t.get("seeding_time") or 0) / 86400, "label": t.get("label") or "", "finished": bool(t.get("is_finished")), "id": tid,
                                                                            "private": t.get("private") if isinstance(t.get("private"), bool) else None}
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

def qbit_session():
    """(base url, cookie jar) of a signed-in qBittorrent session."""
    cfg = CLIENTS["qBittorrent"]; cj = http.cookiejar.CookieJar(); base = cfg["url"] + "/api/v2"
    req = urllib.request.Request(base + "/auth/login", data=urllib.parse.urlencode({"username": cfg["user"], "password": cfg["password"]}).encode(), headers={"Referer": cfg["url"]})
    if "Ok" not in urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)).open(req, timeout=30).read().decode(): raise RuntimeError("the login was refused")
    return base, cj
def read_qbit():
    cfg = CLIENTS["qBittorrent"]
    if not cfg["url"]: return {}
    base, cj = qbit_session()
    out = {}; ts = http_json(base + "/torrents/info", cj=cj) or []
    for t in ts:
        for f in http_json(base + "/torrents/files?hash=" + t["hash"], cj=cj) or []:
            out[map_client_path(os.path.join(t["save_path"], f["name"]))] = {"client": "qBittorrent", "name": t["name"], "state": str(t.get("state", "")).lower(), "ratio": float(t.get("ratio") or 0),
                                                                             "seeding_days": float(t.get("seeding_time") or 0) / 86400, "label": t.get("category") or "", "finished": float(t.get("progress") or 0) >= 1, "id": t["hash"],
                                                                             "private": next((t[k] for k in ("private", "is_private", "isPrivate") if isinstance(t.get(k), bool)), None)}
    CLIENT_STATUS["qBittorrent"] = {"ok": True, "n": len(ts), "note": f"{len(ts)} torrents"}; return out

def api_v(app): return "v1" if app == "lidarr" else "v3"

# ------------------------------------------------------------------------------------------------------------ categories
CATEGORIES = [("tv", "TV shows"), ("anime", "Anime"), ("movies", "Movies"), ("adult", "Adult"), ("sports", "Sports"), ("music", "Music"), ("other", "Other")]
CAT_LABEL = dict(CATEGORIES)
APP_CATEGORY = {"sonarr": "tv", "radarr": "movies", "whisparr": "adult", "sportarr": "sports", "lidarr": "music"}
CAT_WORDS = [("anime", {"anime"}), ("adult", {"xxx", "adult", "whisparr", "porn", "nsfw"}), ("sports", {"sport", "sports", "sportarr"}),
             ("music", {"music", "lidarr"}), ("tv", {"tv", "series", "shows", "sonarr", "television", "episodes"}),
             ("movies", {"movie", "movies", "film", "films", "radarr", "cinema"})]

TWIN_IS_EVIDENCE = {"REAL_COPY", "LINKED_SEED", "LIBRARY_TWIN", "DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER"}   # a lookalike's twin is only a size coincidence

def words_category(text):
    """A category from words in folder names or a client label ('tv-sonarr', 'Anime', 'xxx'); None when nothing matches."""
    toks = set(re.split(r"[^a-z0-9]+", (text or "").lower()))
    for cat, words in CAT_WORDS:
        if toks & words: return cat
    return None

def read_arr_roots():
    """Each arr's library folders (its root folders) and Sonarr's anime series folders: the most reliable answer to 'what kind of file is this'."""
    roots, anime = [], []
    for app, cfg in ARRS.items():
        if not cfg["url"] or not cfg["key"]: continue
        H = {"X-Api-Key": cfg["key"]}
        try:
            for rf in http_json(f"{cfg['url']}/api/{api_v(app)}/rootfolder", headers=H) or []:
                pth = map_client_path(str(rf.get("path") or "")).rstrip("/")
                if pth: roots.append((pth, "anime" if words_category(os.path.basename(pth)) == "anime" else APP_CATEGORY[app]))
            if app == "sonarr":
                for se in http_json(f"{cfg['url']}/api/v3/series", headers=H) or []:
                    if str(se.get("seriesType", "")).lower() == "anime" and se.get("path"): anime.append(map_client_path(str(se["path"])).rstrip("/"))
        except Exception as e: note_error(f"{app} folders: {e}")
    roots.sort(key=lambda r: -len(r[0]))
    return {"roots": roots, "anime": anime}

def category_of(path, ctx, twin=None, ci=None, ai=None):
    """What kind of media a file is, from the strongest evidence available:
    the arr's anime flag, the arr root folder it sits in, the library file it belongs to, the arr that imported it, the client's label, then folder names."""
    roots, anime = (ctx or {}).get("roots", []), (ctx or {}).get("anime", [])
    def by_arr(p):
        if not p: return None
        if any(p.startswith(a + "/") for a in anime): return "anime"
        for r, cat in roots:
            if p.startswith(r + "/"): return cat
        return None
    for p in (path, twin, (ai or {}).get("imported")):
        cat = by_arr(p)
        if cat: return cat
    if ai and ai.get("app") in APP_CATEGORY:
        lab = words_category((ci or {}).get("label"))
        return "anime" if lab == "anime" else APP_CATEGORY[ai["app"]]
    cat = words_category((ci or {}).get("label"))
    if cat: return cat
    for p in (twin, path):   # folders from the top down: 'completed/tv' decides before a release name that happens to say FLAC or Sports
        for seg in (os.path.dirname(p).split("/") if p else []):
            cat = words_category(seg)
            if cat: return cat
    return "other"

def read_arr_imports():
    """What each arr imported from where: dropped path (the download) -> imported path (the library file), newest first."""
    out = {}
    for app, cfg in ARRS.items():
        if not cfg["url"] or not cfg["key"]: continue
        H = {"X-Api-Key": cfg["key"]}; n = 0; page = 1
        try:
            while page <= 40:
                r = http_json(f"{cfg['url']}/api/{api_v(app)}/history?page={page}&pageSize=500&eventType=3&sortKey=date&sortDirection=descending", headers=H)
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

IMPORT_WAIT = {"importPending": "waiting to import it", "importing": "importing it right now", "importBlocked": "cannot import it on its own"}
def read_arr_queue():
    """#20: what each arr is about to import. outputPath (the finished download, mapped) -> {app, state, why, title}. Only waiting/importing/blocked items."""
    out = {}
    for app, cfg in ARRS.items():
        if not cfg["url"] or not cfg["key"]: continue
        try:
            r = http_json(f"{cfg['url']}/api/{api_v(app)}/queue?page=1&pageSize=500&includeUnknownSeriesItems=true&includeUnknownMovieItems=true", headers={"X-Api-Key": cfg["key"]})
            for rec in (r.get("records") or []) if isinstance(r, dict) else []:
                st = rec.get("trackedDownloadState") or ""; op = map_client_path(rec.get("outputPath") or "")
                if st not in IMPORT_WAIT or not op: continue
                msgs = [m for sm in (rec.get("statusMessages") or []) for m in (sm.get("messages") or [])]
                out[op.rstrip("/")] = {"app": app, "state": "importBlocked" if st == "importPending" and rec.get("trackedDownloadStatus") == "warning" and msgs else st, "why": (msgs[0] if msgs else "")[:160], "title": rec.get("title") or ""}
        except Exception as e: note_error(f"{app} queue: {e}")
    return out
def queue_for(path, queue):
    d = path.rstrip("/")
    while len(d) > 1:
        if d in queue: return queue[d]
        d = os.path.dirname(d)
    return None

def gather_context():
    """Ask every configured client and arr once per scan. A client that fails is reported on About and simply leaves its files unexplained."""
    clients = {}
    for name, fn in (("Deluge", read_deluge), ("NZBGet", read_nzbget), ("qBittorrent", read_qbit)):
        if not CLIENTS[name]["url"]: CLIENT_STATUS.pop(name, None); continue
        try: clients.update(fn())
        except Exception as e: CLIENT_STATUS[name] = {"ok": False, "n": 0, "note": str(e)[:160]}; note_error(f"{name}: {e}")
    ctx = {"clients": clients, "imports": read_arr_imports(), "queue": read_arr_queue(), **read_arr_roots(), "clients_ok": any(v["ok"] for k, v in CLIENT_STATUS.items() if k in CLIENTS)}
    try:   # remembered across restarts, so the page and About stay consistent until the next scan asks again
        c = db(); kv_set(c, "client_status", json.dumps(CLIENT_STATUS)); c.close()
    except Exception as e: log(f"could not remember the client status: {e}")
    return ctx

def client_status():
    if CLIENT_STATUS: return CLIENT_STATUS
    try:
        c = db(); saved = kv_get(c, "client_status"); c.close()
        if saved: CLIENT_STATUS.update(json.loads(saved))
    except Exception: pass
    return CLIENT_STATUS

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
    return (r_ok or d_ok) if rule_for(ci) == "either" else (r_ok and d_ok)

def rule_for(ci):
    """The seeding rule for this torrent: 'both' or 'either'. With SEED_RULE=auto (2026-11-13, owner: "some are private but most are public")
    a private torrent needs ratio AND days; a public one either; a client that does not say is treated as private (the safe side)."""
    if SEED_RULE in ("both", "either"): return SEED_RULE
    return "either" if (ci or {}).get("private") is False else "both"

def seed_wait(ci):
    """Short words for the row while a torrent still seeds: when it can go. '' when it can go now or nothing seeds it."""
    if not ci or ci.get("client") == "NZBGet" or seeding_done(ci): return ""
    if not ci.get("finished"): return "Still downloading"
    days_left = max(0.0, SEED_MIN_DAYS - (ci.get("seeding_days") or 0)); ratio_left = (ci.get("ratio") or 0) < SEED_MIN_RATIO
    d_txt = "tomorrow" if days_left <= 1 else f"in about {round(days_left)} days"
    if rule_for(ci) == "either": return f"Still seeding · can go {d_txt}" + (f", or sooner at ratio {SEED_MIN_RATIO:g}" if ratio_left else "")
    if days_left <= 0: return f"Still seeding (private) · can go once ratio reaches {SEED_MIN_RATIO:g}"
    return f"Still seeding{' (private)' if ci.get('private') else ''} · can go {d_txt}" + (f" if ratio has reached {SEED_MIN_RATIO:g}" if ratio_left else "")

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
    CREATE TABLE IF NOT EXISTS held(id INTEGER PRIMARY KEY AUTOINCREMENT, orig TEXT, held TEXT, size INTEGER, dev INTEGER, ino INTEGER, at REAL, kind TEXT, why TEXT,
        restored REAL, purged REAL, note TEXT);
    """)
    cols = {r[1] for r in c.execute("PRAGMA table_info(verdict)")}
    for col, typ in (("client", "TEXT"), ("removable", "INTEGER"), ("category", "TEXT"), ("queue", "TEXT")):
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
                        if e.is_dir(follow_symlinks=False):
                            if os.path.normpath(e.path) != os.path.normpath(HOLDING_DIR): stack.append(e.path)   # the holding bin is not part of the library
                            continue
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
        purge_holding(c, now)
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
    ctx = ctx or {"clients": {}, "imports": {}, "clients_ok": False}; clients, imports, clients_ok = ctx["clients"], ctx["imports"], ctx.get("clients_ok", False); queue = ctx.get("queue") or {}
    extra = {}   # path -> (client info or None, removable)
    extra_q = {}   # path -> what the arr's queue says (#20)
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
            elif (aq := queue_for(r["path"], queue)) and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE"):   # #20: finished, and the arr is about to import it (or is stuck)
                code, twin = "AWAITING_IMPORT", None; ev += f"; {aq['app'].capitalize()} is {IMPORT_WAIT[aq['state']]}" + (f": {aq['why']}" if aq["why"] else ""); extra_q[r["path"]] = aq
            elif ci and not ci.get("finished") and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE"):   # #20: still coming in; neither seeding nor waste yet
                code, twin = "DOWNLOADING", None; ev += "; still downloading"
            elif clients_ok and ci is None and any(a["url"] for a in ARRS.values()): code = "ORPHAN"; ev += "; no download client knows this file and no arr imported it"
            elif ci and ci.get("client") == "NZBGet" and ci.get("finished") and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE") and any(a["url"] for a in ARRS.values()):   # a finished (or failed) usenet job nobody imported holds space for nothing: to decide, never "seeding only"
                if code == "LOOKALIKE": code, twin, ev = "DOWNLOAD_UNMATCHED", None, f"inode {r['ino']}, link count {r['nlink']}, same size as a library file but different content"
                ev += "; no arr ever imported it, and usenet does not seed"
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
        ci, rem = extra.get(path, (None, True)); cat = category_of(path, ctx, twin if code in TWIN_IS_EVIDENCE else None, ci, imports.get(path))
        c.execute("""INSERT INTO verdict(path,code,title,kind,size,wasted,twin,evidence,first_seen,last_seen,client,removable,category,queue) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(path) DO UPDATE SET code=excluded.code,title=excluded.title,kind=excluded.kind,size=excluded.size,wasted=excluded.wasted,
                     twin=excluded.twin,evidence=excluded.evidence,last_seen=excluded.last_seen,client=excluded.client,removable=excluded.removable,category=excluded.category,queue=excluded.queue""",
                  (path, code, friendly_title(path), kind, size, wasted, twin, ev, now, now, json.dumps(ci) if ci else None, 1 if rem else 0, cat, json.dumps(extra_q[path]) if path in extra_q else None))
    for r in c.execute("SELECT path FROM verdict").fetchall():
        if r["path"] not in seen: c.execute("DELETE FROM verdict WHERE path=?", (r["path"],))
    c.commit()
    return sum(v[3] for v in verdicts.values())

# ---------------------------------------------------------------------------------------------------------- plain words
LABEL = {"REAL_COPY": "Real copy", "LIBRARY_ONLY": "Only copy", "LIBRARY_TWIN": "Two names, one file", "LINKED_SEED": "Seeding name",
         "DUPLICATE_COPY": "Stored twice", "DUPLICATE_HEADER": "Stored twice, header differs", "LOOKALIKE": "Looks alike", "DOWNLOAD_UNMATCHED": "Download without a match", "DOWNLOAD_OTHER_RELEASE": "Different release in library",
         "LEFTOVER": "Leftover from an upgrade", "ORPHAN": "Orphan", "SEEDING_ONLY": "Seeding only", "DOWNLOADING": "Still downloading", "AWAITING_IMPORT": "Waiting for import",
         "BIN_OLD": "Old recycle-bin file", "BIN_RECENT": "In the recycle bin", "OTHER": "Unsorted folder"}
NEEDS_YOU = {"DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN", "BIN_OLD", "OTHER"}
def seed_sentence(ci):
    if ci is None: return "Nothing holds it, so it can go now."
    if ci.get("client") == "NZBGet": return "It came from usenet, so there is no seeding to wait for; it can go now."
    if seeding_done(ci): return f"{ci['client']} has seeded it to ratio {ci['ratio']:.1f} over {ci['seeding_days']:.0f} days, past your rule, so it can go now."
    kind = "" if SEED_RULE in ("both", "either") else (" (a private torrent)" if ci.get("private") else " (a public torrent)" if ci.get("private") is False else " (the client does not say whether it is private, so the safe rule)")
    return f"{ci['client']} is still seeding it (ratio {ci['ratio']:.2f}, {ci['seeding_days']:.0f} days); your rule{kind} keeps it until ratio {SEED_MIN_RATIO:g} {'or' if rule_for(ci) == 'either' else 'and'} {SEED_MIN_DAYS:g} days."
def sentence(v):
    code, size, twin = v["code"], human(v["size"]), v.get("twin")
    t = friendly_title(twin) if twin else ""
    ci = v.get("client") if isinstance(v.get("client"), dict) else (json.loads(v["client"]) if v.get("client") else None)
    tail = (" " + seed_sentence(ci)) if (ci or v.get("clients_known")) else ""
    if code == "LEFTOVER": return f"A leftover from an upgrade: this download was imported into the library and the library has since moved on ({t} is what it keeps now). {size} to get back.{tail}"
    if code == "ORPHAN": return f"An orphan: no download client knows this file and no arr imported it. {size} to get back. Nothing holds it, so it can go now."
    if code == "DOWNLOADING": return f"Still downloading in {ci['client'] if ci else 'your download client'}. Nothing to judge yet; it is looked at again once it finishes."
    if code == "AWAITING_IMPORT":
        aq = v.get("queue") if isinstance(v.get("queue"), dict) else (json.loads(v["queue"]) if v.get("queue") else {}); app = (aq.get("app") or "your arr").capitalize()
        if aq.get("state") == "importBlocked": return f"Finished, but {app} cannot import it on its own" + (f" ({aq['why'].rstrip('.')})" if aq.get("why") else "") + f". Open {app}'s Activity queue and import it by hand or remove it there; Dedupearr leaves it alone."
        return f"Finished, and {app} is {IMPORT_WAIT.get(aq.get('state'), 'about to import it')}. Nothing to do; once it is in the library this download is judged like any other."
    if code == "SEEDING_ONLY": return f"Kept for seeding: {ci['client'] if ci else 'your client'} is seeding it and nothing in the library came from it. Not counted as waste; it goes when you remove the torrent."
    if code == "REAL_COPY": return "One file with two names: the library and the download folder share it, so seeding costs no extra space."
    if code == "LINKED_SEED": return f"This is the download-folder name of the library file {t}. Same file, no extra space."
    if code == "LIBRARY_ONLY": return "One copy, in the library. Nothing to do."
    if code == "LIBRARY_TWIN": return f"Two names inside the library share one file ({t} is the other). No space wasted."
    if code == "DUPLICATE_COPY": return f"Stored twice: the library file {t} and this download are two separate files with identical content. Making them one file again would free {size}.{tail}"
    if code == "DUPLICATE_HEADER": return f"Stored twice: the library file {t} and this download are the same video, except the first part of the file differs. That usually means a tag or title was edited on one copy (Auditarr does this when it fixes audio languages). Relinking would not be safe, because the torrent would put the old header back; once seeding is done the download can go. {size} to get back.{tail}"
    if code == "LOOKALIKE": return f"Same size as {t} but different content, so not a duplicate. Worth a look if you expected one copy."
    if code == "DOWNLOAD_UNMATCHED" and ci and ci.get("client") == "NZBGet" and ci.get("finished"):
        how = "marked it failed" if ci.get("state") == "failed" else "finished it" if ci.get("state") == "finished" else "lists it as " + str(ci.get("state") or "done").replace("/", ", ")
        return f"A usenet download nothing in the library matches and no arr imported: NZBGet {how}. Usenet does not seed, so nothing holds it; import it if you want it, otherwise it can go. {size} to decide on."
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
    elif kind == "remove": steps = ([f"Ask {ci['client']} once more whether the torrent '{ci['name']}' is still past your seeding rule, then remove it there without deleting its files." if ci and ci.get("client") != "NZBGet" else ("It came from usenet and nothing seeds it; NZBGet's history is left as it is." if ci else "No client holds this file, so there is nothing to tell a client.")]
                                    + [f"Move the file into the holding bin at {hold} (nothing is deleted).", f"Keep it for {HOLD_DAYS} days, then delete it, unless you press Undo."])
    elif kind == "bin": steps = [f"Move the file into the holding bin at {hold} (nothing is deleted).", f"Keep it for {HOLD_DAYS} days, then delete it, unless you press Undo."]
    else: steps = [f"The file has sat in its recycle bin longer than your {RECYCLE_DAYS}-day rule.", "Delete it for good (it was already thrown away once; there is no second bin)."]
    return kind, steps, v.get("wasted") or 0

def record_action(c, mode, kind, path, detail, result, undo=None):
    c.execute("INSERT INTO action(ts,mode,kind,path,detail,result,undo) VALUES(?,?,?,?,?,?,?)", (time.time(), mode, kind, path, detail[:500], result[:500], json.dumps(undo) if undo else None)); c.commit()

# ------------------------------------------------------------------------------------------------------- holding bin (P3.1)
READ_ONLY_NOTE = "Dedupearr can only read your media folder (it is mounted read-only), so nothing was moved. To let fixes move files, mount the share read-write; dry runs work either way."
def hold_target(path, now):
    """Where a file waits in the holding bin: HOLDING_DIR/<day>/<its full path>, so two files with one name never collide and the way back is plain to see."""
    return os.path.join(HOLDING_DIR, time.strftime("%Y-%m-%d", time.localtime(now)), os.path.normpath(path).lstrip(os.sep))
def under_roots(path):
    n = os.path.normpath(path); return any(n.startswith(os.path.normpath(r) + os.sep) for r in SCAN_ROOTS) and not n.startswith(os.path.normpath(HOLDING_DIR) + os.sep)
def hold_file(c, path, kind, why, now=None):
    """Move one file into the holding bin after checking it is still the file the scan judged. Returns (code, obj). Nothing is ever deleted here."""
    now = now or time.time()
    if not under_roots(path): return 400, {"error": "that file is outside the folders Dedupearr scans"}
    f = c.execute("SELECT path, dev, ino, size FROM file WHERE path=?", (path,)).fetchone()   # only a file the scan itself recorded, and from here on the recorded path (CodeQL #3-#8)
    if not f: return 409, {"error": "the last scan has no record of that file, so nothing was moved; scan again and look at the row once more"}
    path = f["path"]
    try: st = os.lstat(path)
    except FileNotFoundError: return 409, {"error": "the file is no longer there; the next scan drops the row"}
    if not stat_regular(st): return 400, {"error": "that is not a plain file, so it is left alone"}
    if (st.st_dev, st.st_ino, st.st_size) != (f["dev"], f["ino"], f["size"]): return 409, {"error": "the file changed since the last scan, so nothing was moved; scan again and look at the row once more"}
    dest = hold_target(path, now)
    if os.path.lexists(dest): return 409, {"error": "the holding bin already holds a file at that place; nothing was moved"}
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.stat(os.path.dirname(dest)).st_dev != st.st_dev: return 409, {"error": "the holding bin is on another disk than the file, so moving would mean copying; nothing was moved. Set HOLDING_DIR inside the same share."}
        os.rename(path, dest)
    except OSError as e:
        if e.errno in (30, 13, 1): return 409, {"error": READ_ONLY_NOTE}   # EROFS, EACCES, EPERM
        return 500, {"error": f"the move failed, nothing was changed: {e.strerror or e}"}
    hid = c.execute("INSERT INTO held(orig,held,size,dev,ino,at,kind,why) VALUES(?,?,?,?,?,?,?,?)", (path, dest, st.st_size, st.st_dev, st.st_ino, now, kind, why[:300])).lastrowid
    c.execute("DELETE FROM verdict WHERE path=?", (path,)); c.execute("DELETE FROM file WHERE path=?", (path,)); c.commit()
    record_action(c, "on", kind, path, why, f"moved to the holding bin; kept {HOLD_DAYS} days, Put back undoes it", {"held_id": hid})
    return 200, {"ok": True, "dry_run": False, "held_id": hid, "dest": dest, "frees": st.st_size, "frees_h": human(st.st_size),
                 "note": f"Moved to the holding bin. {human(st.st_size)} comes back after {HOLD_DAYS} days; Put back (Actions tab) undoes it until then."}
def stat_regular(st):
    import stat as _st; return _st.S_ISREG(st.st_mode)
def restore_held(c, hid):
    """Put a held file back where it was. Refuses when its old place is taken or the file in the bin is not the one that went in."""
    r = c.execute("SELECT * FROM held WHERE id=?", (hid,)).fetchone()
    if not r: return 404, {"error": "nothing like that in the holding bin"}
    if r["restored"]: return 409, {"error": "already put back"}
    if r["purged"]: return 409, {"error": f"its {HOLD_DAYS} days ran out and it was deleted; it cannot come back"}
    try: st = os.lstat(r["held"])
    except FileNotFoundError: return 409, {"error": "the file is no longer in the holding bin (someone moved it by hand)"}
    if (st.st_ino, st.st_size) != (r["ino"], r["size"]): return 409, {"error": "the file in the holding bin is not the one that went in, so it is left alone"}
    if os.path.lexists(r["orig"]): return 409, {"error": "something new already sits at its old place, so nothing was moved; move that away first"}
    try: os.makedirs(os.path.dirname(r["orig"]), exist_ok=True); os.rename(r["held"], r["orig"])
    except OSError as e: return (409, {"error": READ_ONLY_NOTE}) if e.errno in (30, 13, 1) else (500, {"error": f"putting it back failed, nothing was changed: {e.strerror or e}"})
    c.execute("UPDATE held SET restored=? WHERE id=?", (time.time(), hid)); c.commit(); tidy_holding(os.path.dirname(r["held"]))
    record_action(c, "on", "undo", r["orig"], f"put back from the holding bin (held {datetime.datetime.fromtimestamp(r['at']):%Y-%m-%d %H:%M})", "back in its old place; the next scan judges it again")
    return 200, {"ok": True, "note": "Put back in its old place. The next scan judges it again."}
def tidy_holding(d):
    """Remove empty folders the holding bin made, never the bin itself."""
    top = os.path.normpath(HOLDING_DIR)
    while d and os.path.normpath(d).startswith(top + os.sep):
        try: os.rmdir(d)
        except OSError: return
        d = os.path.dirname(d)
def purge_holding(c, now=None):
    """Delete what has waited HOLD_DAYS in the holding bin, but only a file that is still the one that went in. Returns (files, bytes)."""
    now = now or time.time(); n = b = 0
    for r in c.execute("SELECT * FROM held WHERE restored IS NULL AND purged IS NULL AND at < ?", (now - HOLD_DAYS * 86400,)).fetchall():
        try: st = os.lstat(r["held"])
        except FileNotFoundError: c.execute("UPDATE held SET purged=?, note=? WHERE id=?", (now, "already gone from the bin", r["id"])); continue
        if (st.st_ino, st.st_size) != (r["ino"], r["size"]): c.execute("UPDATE held SET note=? WHERE id=?", ("left alone: not the file that went in", r["id"])); continue
        try: os.remove(r["held"])
        except OSError as e: note_error(f"holding bin: could not delete {r['held']}: {e}"); continue
        c.execute("UPDATE held SET purged=? WHERE id=?", (now, r["id"])); n += 1; b += r["size"]; tidy_holding(os.path.dirname(r["held"]))
        record_action(c, "on", "purge", r["orig"], f"{HOLD_DAYS} days in the holding bin ran out", f"deleted, {human(r['size'])} back")
    c.commit()
    if n: log(f"holding bin: {n} file(s) past {HOLD_DAYS} days deleted, {human(b)} back")
    return n, b
def holding(c, now=None):
    now = now or time.time(); out = []
    for r in c.execute("SELECT * FROM held WHERE restored IS NULL AND purged IS NULL ORDER BY at DESC").fetchall():
        left = max(0.0, HOLD_DAYS - (now - r["at"]) / 86400)
        out.append({"id": r["id"], "orig": r["orig"], "title": friendly_title(r["orig"]), "size": r["size"], "size_h": human(r["size"]), "kind": r["kind"], "why": r["why"],
                    "when": datetime.datetime.fromtimestamp(r["at"]).isoformat(timespec="minutes"), "days_left": round(left, 1), "note": r["note"] or ""})
    return {"items": out, "bytes": sum(i["size"] for i in out), "bytes_h": human(sum(i["size"] for i in out)), "days": HOLD_DAYS, "dir": HOLDING_DIR}

REMOVE_READERS = {"Deluge": lambda: read_deluge(), "qBittorrent": lambda: read_qbit()}
def client_remove(ci):
    """Tell the client to forget one torrent and keep its files (the file itself is already in the holding bin)."""
    if ci["client"] == "Deluge":
        if deluge_session()("core.remove_torrent", [ci["id"], False]) is False: raise RuntimeError("Deluge refused to remove the torrent")
    elif ci["client"] == "qBittorrent":
        base, cj = qbit_session(); http_json(base + "/torrents/delete", {"hashes": ci["id"], "deleteFiles": "false"}, cj=cj, form=True)
    else: raise RuntimeError(f"no remove call for {ci['client']}")
def remove_fix(c, v, steps, now=None):
    """P3.2: a leftover or second copy whose seeding is done. The file goes to the holding bin first; then the client is told to forget the
    torrent (never its files); if the client refuses, the file goes straight back. Returns (code, obj)."""
    path = v["path"]; ci = json.loads(v["client"]) if isinstance(v.get("client"), str) and v["client"] else (v.get("client") or None)
    why = f"{v['code']}: " + " | ".join(steps); torrent = None
    if ci and ci.get("client") in REMOVE_READERS:
        try: fresh = REMOVE_READERS[ci["client"]]()
        except Exception as e: return 502, {"error": f"{ci['client']} did not answer just now ({e}), so nothing was changed; try again in a minute"}
        mine = {p: i for p, i in fresh.items() if i.get("id") == ci.get("id") and i.get("client") == ci["client"]}
        if mine:   # the torrent is still there: judge it on what the client says now, not on last night's scan
            cur = mine.get(path) or next(iter(mine.values()))
            if not seeding_done(cur): return 409, {"error": "the client says this torrent is not past your seeding rule any more, so nothing was changed. " + seed_sentence(cur)}
            others = [p for p in mine if p != path and os.path.exists(p) and os.path.getsize(p) >= MIN_SIZE_MB * 1024 * 1024]
            if others: return 409, {"error": f"this torrent holds {len(others)} other large file(s); removing it would stop seeding those too, so nothing was changed. Fixing a whole torrent at once arrives with 'Fix all with this cause'."}
            torrent = cur
    code, obj = hold_file(c, path, "remove", why, now)
    if code != 200: return code, obj
    if torrent:
        try: client_remove(torrent)
        except Exception as e:
            back, _ = restore_held(c, obj["held_id"])
            record_action(c, "on", "remove", path, why, f"{torrent['client']} would not remove the torrent ({e}); the file was put back" if back == 200 else f"{torrent['client']} would not remove the torrent ({e}); the file waits in the holding bin, Put back returns it")
            return 502, {"error": f"{torrent['client']} would not remove the torrent ({e}). " + ("The file is back in its place; nothing was changed." if back == 200 else "The file waits in the holding bin; Put back (Actions tab) returns it.")}
        c.execute("UPDATE held SET note=? WHERE id=?", (f"its torrent '{torrent['name']}' was removed from {torrent['client']}; Put back returns the file, not the torrent", obj["held_id"])); c.commit()
        record_action(c, "on", "remove", path, f"removed the torrent '{torrent['name']}' from {torrent['client']} (files kept)", "done; the file waits in the holding bin")
        obj["note"] = f"Removed the torrent from {torrent['client']} and moved the file to the holding bin. {obj['frees_h']} comes back after {HOLD_DAYS} days; Put back (Actions tab) returns the file until then, not the torrent."
    return 200, obj
LIVE_KINDS = {"bin", "remove"}   # P3.1: orphans to the holding bin. P3.2 (2026-11-14): remove a leftover or second copy through the client. Relink and emptying the recycle bin stay dry runs until their gates pass
def ui_fix(c, path):
    """POST /api/fix: show the plan (dry run) or carry it out (on). Phase 3 ships the 'on' branch fix by fix; until then every call is a dry run."""
    if SCANNING["on"]: return 409, {"error": "a scan is running; try again in a minute, so a fix never races the scan"}
    r = c.execute("SELECT * FROM verdict WHERE path=?", (path,)).fetchone()
    if not r: return 404, {"error": "that file is not in the last scan"}
    v = dict(r); v["removable"] = bool(v.get("removable"))
    kind, steps, frees = plan_fix(v)
    if not kind: return 400, {"error": steps[0]}
    if FIX_MODE == "off": return 403, {"error": "fixes are switched off (FIX_MODE=off); the plan is still shown on the row"}
    if FIX_MODE == "on" and kind in LIVE_KINDS:
        code, obj = remove_fix(c, v, steps) if kind == "remove" else hold_file(c, path, kind, f"{v['code']}: " + " | ".join(steps))
        if code != 200: record_action(c, "on", kind, path, " | ".join(steps), "refused: " + obj.get("error", ""))
        return code, dict(obj, kind=kind, steps=steps)
    later = FIX_MODE == "on" and kind not in LIVE_KINDS
    record_action(c, "dryrun", kind, path, " | ".join(steps), f"dry run: would free {human(frees)}")
    return 200, {"ok": True, "dry_run": True, "kind": kind, "steps": steps, "frees": frees, "frees_h": human(frees),
                 "note": f"Dry run: nothing was changed. This fix would free {human(frees)} in {len(steps)} steps." + (" This kind of fix is not switched on yet; it arrives after its own safety checks." if later else "")}

def actions(c, limit=100):
    return [dict(r) | {"when": datetime.datetime.fromtimestamp(r["ts"]).isoformat(timespec="minutes")} for r in c.execute("SELECT * FROM action ORDER BY ts DESC LIMIT ?", (limit,))]

def state(c):
    items = []; known = bool(client_status())   # once per page, not once per row (the perf budget caught the per-row lookup)
    if not known:   # upgraded from a build that did not remember the status yet: the rows still carry what the clients said
        known = c.execute("SELECT 1 FROM verdict WHERE client IS NOT NULL AND client != '' LIMIT 1").fetchone() is not None
    ignored = {r["path"] for r in c.execute("SELECT path FROM ignored")}
    for r in c.execute("SELECT * FROM verdict ORDER BY wasted DESC, size DESC, path"):
        v = dict(r); v["clients_known"] = known; v["label"] = LABEL.get(v["code"], v["code"]); v["plain"] = sentence(v); v["size_h"] = human(v["size"]); v["wasted_h"] = human(v["wasted"])
        v["needs_you"] = v["code"] in NEEDS_YOU and v["path"] not in ignored; v["ignored"] = v["path"] in ignored
        v["twin_title"] = friendly_title(v["twin"]) if v["twin"] else None
        ci = json.loads(v["client"]) if v.get("client") else None; v["client"] = ci; v["client_text"] = client_text(ci) if (ci or (v["kind"] == "download" and known)) else ""
        v["removable"] = bool(v.get("removable")) and v["needs_you"]
        v["category"] = v.get("category") if v.get("category") in CAT_LABEL else "other"; v["category_label"] = CAT_LABEL[v["category"]]
        v["fixable"] = v["code"] in FIXABLE and v["needs_you"] and (v["code"] in ("ORPHAN", "BIN_OLD") or v["removable"])
        v["fix_kind"] = FIXABLE.get(v["code"]) if v["fixable"] else ""
        v["wait"] = seed_wait(v.get("client") if isinstance(v.get("client"), dict) else None) if v["needs_you"] and not v["fixable"] and v["code"] in FIXABLE else ""   # why there is no button yet
        items.append(v)
    counts, bytes_by = {}, {}
    cats = {k: {"key": k, "label": lab, "files": 0, "needs": 0, "wasted": 0} for k, lab in CATEGORIES}
    for v in items:
        cc = cats[v["category"]]; cc["files"] += 1; cc["needs"] += 1 if v["needs_you"] else 0; cc["wasted"] += v["wasted"]
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
            + (f" {human(now_bytes)} of it can go right now by your seeding rule." if now_bytes and known else "")) if last else "No scan yet. Press Scan now, or wait for the night."
    return {"app": APP, "tagline": TAGLINE, "phase": PHASE, "version": VERSION, "revision": REVISION, "read_only": FIX_MODE != "on", "sandbox": SANDBOX, "sandbox_note": SANDBOX_NOTE if SANDBOX else "",
            "roots": SCAN_ROOTS, "disks": disks, "scanning": {k: SCANNING[k] for k in ("on", "phase", "files", "started")},
            "last_run": dict(last) if last else None, "counts": counts, "wasted_by": bytes_by, "wasted_by_h": {k: human(b) for k, b in bytes_by.items()},
            "library_bytes": library_bytes, "library_h": human(library_bytes), "reclaim_bytes": reclaim, "reclaim_h": human(reclaim),
            "decide_bytes": decide, "decide_h": human(decide), "now_bytes": now_bytes, "now_h": human(now_bytes), "lead": lead, "labels": LABEL, "items": items, "log": LOG[-40:],
            "categories": [dict(cc, wasted_h=human(cc["wasted"])) for cc in cats.values() if cc["files"]],
            "clients": client_status(), "seed_rule": {"ratio": SEED_MIN_RATIO, "days": SEED_MIN_DAYS, "rule": SEED_RULE}, "fix_mode": FIX_MODE, "live_kinds": sorted(LIVE_KINDS), "holding_dir": HOLDING_DIR, "holding": holding(c), "nightly_bot": nightly_bot()}

def nightly_bot():
    """The nightly page bot's last report (written by the server's user script into CONFIG_DIR/uibot.json): passed/total/failed, the bot's build and the page's build."""
    try: r = json.load(open(os.path.join(CONFIG_DIR, "uibot.json")))
    except Exception: return {}
    r["drift"] = f"bot build {str(r.get('revision', ''))[:7]} checked page build {str(r.get('page_revision', ''))[:7]}" if r.get("revision") and r.get("page_revision") and str(r["revision"])[:7] != str(r["page_revision"])[:7] else ""
    r["ok"] = bool(r.get("total")) and not r.get("failed") and not r["drift"]; return r

def about(c):
    last = c.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone(); health = []
    nb = nightly_bot()
    if nb: health.append({"level": "ok" if nb["ok"] else "warn", "text": (f"The nightly page check passed {nb.get('passed')}/{nb.get('total')} at {str(nb.get('ts', ''))[:16].replace('T', ' ')}." if nb["ok"] else
                           f"The nightly page check did not pass: {nb.get('passed')}/{nb.get('total')}" + (", failing: " + ", ".join(nb.get("failed") or [])[:160] if nb.get("failed") else "") + (f"; {nb['drift']} - pull both images together" if nb["drift"] else "") + ".")})
    if not last: health.append({"level": "warn", "text": "No scan has run yet."})
    elif last["note"] != "ok": health.append({"level": "warn", "text": f"The last scan failed: {last['note']}"})
    elif time.time() - last["finished"] > 2 * 86400: health.append({"level": "warn", "text": "The last scan is more than two days old."})
    else: health.append({"level": "ok", "text": f"Last scan {datetime.datetime.fromtimestamp(last['finished']):%Y-%m-%d %H:%M}, {last['files']} files in {int(last['finished'] - last['started'])} s."})
    for r in SCAN_ROOTS:
        if not os.path.isdir(r): health.append({"level": "warn", "text": f"Scan root {r} is not mounted or not a folder."})
    errs = errors_recent()
    health.append({"level": "warn", "text": f"{len(errs)} error{'s' if len(errs) != 1 else ''} in the last 24 hours; the newest: {errs[-1]['text'][:120]}"} if errs else {"level": "ok", "text": "No errors in the last 24 hours."})
    for name, st_ in sorted(client_status().items()): health.append({"level": "ok" if st_["ok"] else "warn", "text": f"{name}: {st_['note']}" if st_["ok"] else f"{name} could not be read: {st_['note']}"})
    if not any(c["url"] for c in CLIENTS.values()) and not any(a["url"] for a in ARRS.values()): health.append({"level": "warn", "text": "No download client or arr is connected, so downloads without a match stay undecided. Set DELUGE_URL / NZBGET_URL / QBIT_URL and SONARR_URL / RADARR_URL (see the README)."})
    elif not client_status() and last: health.append({"level": "ok", "text": "Clients and arrs are configured; they are asked on the next scan."})
    health.append({"level": "ok", "text": {"off": "Fixes are switched off.", "dryrun": "Fixes run as a dry run: every 'Preview fix' shows the exact steps and nothing is moved, linked or deleted.", "on": "Fixes are on: files move to the holding bin first and can be undone for %d days." % HOLD_DAYS}.get(FIX_MODE, FIX_MODE)})
    if SANDBOX: health.append({"level": "ok", "text": SANDBOX_NOTE})
    return {"app": APP, "tagline": TAGLINE, "version": VERSION, "revision": REVISION, "phase": PHASE, "health": health,
            "settings": {"SCAN_ROOTS": SCAN_ROOTS, "LIBRARY_DIRS": LIBRARY_DIRS, "DOWNLOAD_DIRS": DOWNLOAD_DIRS, "RECYCLE_DIRS": RECYCLE_DIRS,
                         "RECYCLE_DAYS": RECYCLE_DAYS, "MIN_SIZE_MB": MIN_SIZE_MB, "HASH_BUDGET_MB": HASH_BUDGET_MB, "SCAN_TIME": SCAN_TIME,
                         "SEED_MIN_RATIO": SEED_MIN_RATIO, "SEED_MIN_DAYS": SEED_MIN_DAYS, "SEED_RULE": SEED_RULE, "CLIENT_PATH_MAP": CLIENT_PATH_MAP, "FIX_MODE": FIX_MODE, "HOLDING_DIR": HOLDING_DIR, "HOLD_DAYS": HOLD_DAYS,
                         "connections": {**{a: (c["url"] or "not set") for a, c in ARRS.items()}, **{n: (c["url"] or "not set") for n, c in CLIENTS.items()}}},
            "runs": [dict(r) for r in c.execute("SELECT * FROM run ORDER BY id DESC LIMIT 30")], "log": LOG[-60:], "errors": errors_recent()[-30:],
            "links": {"source": "https://github.com/chrizantoz/dedupearr", "auditarr": "https://github.com/chrizantoz/auditarr"}}

# ------------------------------------------------------------------------------------------------------------------- sign-in
# Off until an account exists: set on the About tab (PBKDF2 hash in the database) or by AUTH_USER/AUTH_PASSWORD on the container
# (the env wins). With an account the page asks for a sign-in, except: the container itself, a proxy's Remote-User header when
# TRUST_PROXY_AUTH=1, and with "no sign-in on my own network" (About tab or LOCAL_NO_LOGIN=1) a browser on your own network that
# talks to the port directly (no reverse-proxy headers), so the domain asks and the home address does not.
AUTH_USER, AUTH_PASSWORD = _env("AUTH_USER"), os.environ.get("AUTH_PASSWORD", "")
TRUST_PROXY_AUTH = _env("TRUST_PROXY_AUTH", "0") == "1"
LOCAL_NO_LOGIN_ENV = _env("LOCAL_NO_LOGIN", "")   # 1 / 0 = set on the container and wins; empty = the About tab decides
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP", "Forwarded", "CF-Connecting-IP", "CF-Ray", "True-Client-IP", "Via", "Tailscale-Funnel-Request")
ROUTER_WARNING = "Only for a page your visitors reach from outside through a reverse proxy or tunnel (Nginx Proxy Manager, SWAG, Traefik, Caddy, Cloudflare Tunnel). If your router forwards this port straight to the app, leave it off: some routers make every outside visitor look like they are at home."
AUTH_CACHE = {}
def _pbkdf(password, salt): return hashlib.pbkdf2_hmac("sha256", (password or "").encode(), salt.encode(), 200_000).hex()
def _env_fp():
    """The container-settings password, slow-hashed like a page-set one (CodeQL #11/#12: a plain SHA-256 was quick to brute-force). Worked out once per password."""
    if "env-last" not in AUTH_CACHE or AUTH_CACHE["env-last"][0] != (AUTH_USER, AUTH_PASSWORD): AUTH_CACHE["env-last"] = ((AUTH_USER, AUTH_PASSWORD), _pbkdf(AUTH_PASSWORD, "dedupearr-env:" + AUTH_USER))
    return AUTH_CACHE["env-last"][1]
def account():
    """{"user", "source": "env"|"page", "fp": a fingerprint that changes with the password} or None (open page)."""
    if AUTH_USER and AUTH_PASSWORD: return {"user": AUTH_USER, "source": "env", "fp": _env_fp()}
    if "page" not in AUTH_CACHE:
        c = db()
        try: v = kv_get(c, "auth"); AUTH_CACHE["page"] = json.loads(v) if v else None
        finally: c.close()
    a = AUTH_CACHE["page"]
    return {"user": a["user"], "source": "page", "fp": a["hash"], "salt": a["salt"]} if a else None
def save_account(user, password):
    """Set (or with an empty password remove) the page account. Returns the stored record, never the password."""
    import secrets
    c = db()
    try:
        if not password: c.execute("DELETE FROM kv WHERE k='auth'"); c.commit(); AUTH_CACHE["page"] = None; return None
        if len(password) < 8: raise ValueError("the password needs at least 8 characters")
        user = (user or "admin").strip()[:64] or "admin"; salt = secrets.token_hex(16); rec = {"user": user, "salt": salt, "hash": _pbkdf(password, salt)}
        kv_set(c, "auth", json.dumps(rec)); AUTH_CACHE["page"] = rec; return rec
    finally: c.close()
def auth_on(): return account() is not None and not SANDBOX
def local_no_login():
    if LOCAL_NO_LOGIN_ENV in ("0", "1"): return LOCAL_NO_LOGIN_ENV == "1"
    c = db()
    try: return kv_get(c, "local_no_login") == "1"
    finally: c.close()
def direct_local(ip, headers):
    """A private, loopback or tailnet address AND none of the headers a reverse proxy, tunnel or CDN adds. Through a domain one of
    them is always there, so the domain keeps asking even though the proxy sits on a private address."""
    import ipaddress
    if any(headers.get(h) for h in PROXY_HEADERS): return False
    try: a = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError: return False
    if getattr(a, "ipv4_mapped", None): a = a.ipv4_mapped
    return a.is_private or a.is_loopback or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10"))
def session_secret():
    c = db()
    try:
        v = kv_get(c, "session_secret")
        if not v: import secrets; v = secrets.token_hex(32); kv_set(c, "session_secret", v)
        return v
    finally: c.close()
def session_cookie(days=30, now=None):
    import hmac; a = account(); exp = int((now or time.time()) + days * 86400); msg = f"{a['user']}|{exp}"
    return f"{msg}|{hmac.new(session_secret().encode(), (msg + '|' + a['fp']).encode(), 'sha256').hexdigest()}"
def session_user(cookie_header):
    """The signed-in user from the cookie, or None. A new password signs every browser out."""
    import hmac
    a = account()
    if not a: return None
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k != "dedupearr_s" or v.count("|") != 2: continue
        user, exp, sig = v.split("|")
        good = hmac.new(session_secret().encode(), f"{user}|{exp}|{a['fp']}".encode(), "sha256").hexdigest()
        if hmac.compare_digest(sig, good) and user == a["user"] and exp.isdigit() and int(exp) > time.time(): return user
    return None
def check_password(user, password):
    import hmac
    a = account()
    if not a: return False
    pw = _pbkdf(password, "dedupearr-env:" + AUTH_USER) if a["source"] == "env" else _pbkdf(password, a["salt"])
    return hmac.compare_digest((user or "").encode(), a["user"].encode()) & hmac.compare_digest(pw.encode(), a["fp"].encode())
def auth_state(who):
    a = account()
    return {"enabled": auth_on(), "user": a["user"] if a else "", "source": a["source"] if a else "", "local_no_login": local_no_login(),
            "local_env": LOCAL_NO_LOGIN_ENV in ("0", "1"), "via": who if who in ("lan", "local") else "", "warning": ROUTER_WARNING}
LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dedupearr - sign in</title>__ICON__
<style>:root{color-scheme:dark}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0f1419;color:#e6e9ee;font:15px/1.5 system-ui,sans-serif}
form{background:#141b22;border:1px solid #263039;border-radius:12px;padding:28px;width:min(340px,calc(100vw - 32px));box-sizing:border-box}h1{margin:0 0 16px;font-size:22px}
label{display:block;margin:12px 0 4px;color:#b6c0cb}input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #263039;background:#0b0f14;color:#e6e9ee;font:inherit}
button{margin-top:18px;width:100%;padding:10px;border:0;border-radius:8px;background:#38bdf8;color:#0b0f14;font:600 15px system-ui,sans-serif;cursor:pointer}.err{color:#f87171;margin:10px 0 0}.hint{color:#95a0ad;font-size:13px}</style></head>
<body><form method="post" action="login"><h1>Dedupearr</h1><label for="u">User</label><input id="u" name="user" autocomplete="username" autofocus><label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password">__ERR__<button>Sign in</button>
<p class="hint">Forgot it? Set <code>AUTH_USER</code> and <code>AUTH_PASSWORD</code> on the container: they replace the password set on the page.</p></form></body></html>"""

# ------------------------------------------------------------------------------------------------------------------- http
ICON_LINK = '<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAACNElEQVR42u2bO27DMAyGRUFbhy5ZOmY0kCvkGEXOaPQYuUKAjBm9ePHQNe3QqkhcS9aDpF7m4iWO+f2kJFKWQXjY6+7tSxRg0ziA62+hFuhQMWTN8C4MUCu4azbIVuBNbLIVeBOjFI0bcES/62/e91xPe5b5ALDhQ2BTiTKNA6AIQAlNKUa0AC7gMY5S//80DgCY8NTjluK5QQLMneAAp/LBW4DHB6cAx/ZHYo/JXMQgEaBGUxiK5zAHJBEghRDYwzBYgOtp/88Z7CXKtQ6IESUqAzSczQGqiRMr2xS2MyX1AmgC2JyMEYRjTkEVADv6HOW2yhGcc8VR1OBUqwCWEF69wBpwDnWArw/RQ4C7CsRecWRJ8BTPb74ZkpTjsYS+QGE6sTVDWzPUeDNEAXuWB3G8X/IuhW3OxYhyloe/6+79M/9mCDMLNLy28eNFCCHQhciyGZrDazveL6LrM5oDyJqh32jP4SmaIYUJjxGVcQH+J+2XJ9rY7EPfEsOGp15xVA7gNnjTpIclBEsztBZZX/hkzVBIxDWcCRITPsQ/stfjLmAx8Mlej/usAjbAUPgsDkj4LEFLRY1JgDk89aZrtACujpoqO1uhU2Q3SNn+Um24AMfx2K6/WbNgKfpcG67AeT7YZdxzG+uusIadX1MatHBCPJsMyFIAnw+MarNpHGDLgE0A4fedXU3p/5QBLYnwyLoNAZMyLUR/MQNqFmGJzQpbS5VoC6oMvbEG+NUMKDUjfAL3DbciWR5v94twAAAAAElFTkSuQmCC">'
UI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, code, obj, cookie=None):
        b = json.dumps(obj, default=str).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try: return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError: return {}
    def _auth(self):
        """-> the user ('' when the page is open), or None when a sign-in is needed."""
        if not auth_on(): return ""
        ip = self.client_address[0]
        u = session_user(self.headers.get("Cookie"))
        if u: return u
        if ip in ("127.0.0.1", "::1") and not any(self.headers.get(h) for h in PROXY_HEADERS): return "local"
        if direct_local(ip, self.headers) and local_no_login(): return "lan"
        if TRUST_PROXY_AUTH and (self.headers.get("Remote-User") or self.headers.get("X-Remote-User")): return self.headers.get("Remote-User") or self.headers.get("X-Remote-User")
        return None
    def _login_page(self, bad=False):
        b = LOGIN_HTML.replace("__ICON__", ICON_LINK).replace("__ERR__", '<p class="err">Wrong user or password.</p>' if bad else "").encode()
        self.send_response(401 if bad else 200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _redirect(self, to, cookie=None):
        self.send_response(303); self.send_header("Location", to); self.send_header("Cache-Control", "no-store")
        if cookie is not None: self.send_header("Set-Cookie", cookie)
        self.end_headers()
    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p == "/login": return self._login_page()
            if p == "/logout": return self._redirect("login", "dedupearr_s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
            who = self._auth() if p != "/health" else ""
            if who is None: return self._json(401, {"error": "sign in first"}) if p.startswith("/api/") else self._redirect("login")
            if p in ("/", "/index.html"):
                b = open(UI_PATH, "rb").read(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
            c = db()
            try:
                if p == "/api/state": return self._json(200, dict(state(c), auth=auth_state(who)))
                if p == "/api/about": return self._json(200, about(c))
                if p == "/api/actions": return self._json(200, {"actions": actions(c), "fix_mode": FIX_MODE})
                if p == "/health":
                    last = c.execute("SELECT finished, note FROM run ORDER BY id DESC LIMIT 1").fetchone()
                    ok = last and last["note"] == "ok" and time.time() - last["finished"] < 2 * 86400; nb = nightly_bot()
                    return self._json(200 if (ok or not last) and (not nb or nb["ok"]) else 503, {"ok": bool(ok) and (not nb or nb["ok"]), "version": VERSION, "revision": REVISION, "nightly_bot": {k: nb.get(k) for k in ("ts", "passed", "total", "ok")} if nb else None})
            finally: c.close()
            self._json(404, {"error": "not found"})
        except Exception as e:
            note_error("request failed: " + str(e)); self._json(500, {"error": str(e)})
    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/login":
            n = int(self.headers.get("Content-Length") or 0); form = urllib.parse.parse_qs((self.rfile.read(n) if 0 < n < 4096 else b"").decode("utf-8", "replace"))
            if auth_on() and check_password((form.get("user") or [""])[0], (form.get("password") or [""])[0]):
                secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
                return self._redirect("./", f"dedupearr_s={session_cookie()}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000{secure}")
            time.sleep(1.5); log(f"sign-in refused from {self.client_address[0]}"); return self._login_page(bad=True)
        who = self._auth()
        if who is None: return self._json(401, {"error": "sign in first"})
        body = self._body()
        try:
            if p in ("/api/setpassword", "/api/local_login") and SANDBOX: return self._json(403, {"error": SANDBOX_NOTE})
            if p == "/api/setpassword":
                a = account()
                if a and a["source"] == "env": return self._json(409, {"error": "the sign-in is set by AUTH_USER and AUTH_PASSWORD on the container; change it there"})
                if a and not check_password(a["user"], str(body.get("current") or "")): return self._json(403, {"error": "the current password is needed to change it"})
                ip = self.client_address[0]
                if not a and not (direct_local(ip, self.headers) or (TRUST_PROXY_AUTH and self.headers.get("Remote-User"))):
                    return self._json(403, {"error": "set the first password from your own network (the page's address and port), not through the internet"})
                try: rec = save_account(str(body.get("user") or "admin"), str(body.get("password") or "") if not body.get("off") else "")
                except ValueError as e: return self._json(400, {"error": str(e)})
                c = db()
                try: record_action(c, "on", "settings", "sign-in", f"by {who or ip}", "sign-in turned on" if rec else "sign-in turned off")
                finally: c.close()
                if not rec: return self._json(200, {"ok": True, "note": "Sign-in is off: anyone who can reach the page can open it."})
                secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
                return self._json(200, {"ok": True, "note": "Sign-in is on. This browser stays signed in; others are asked."}, cookie=f"dedupearr_s={session_cookie()}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000{secure}")
            if p == "/api/local_login":
                if LOCAL_NO_LOGIN_ENV in ("0", "1"): return self._json(409, {"error": f"LOCAL_NO_LOGIN={LOCAL_NO_LOGIN_ENV} is set on the container; change it there"})
                on = bool(body.get("on")); c = db()
                try: kv_set(c, "local_no_login", "1" if on else "0"); record_action(c, "on", "settings", "sign-in", f"by {who or self.client_address[0]}", "no sign-in on the home network" if on else "sign-in everywhere")
                finally: c.close()
                return self._json(200, {"ok": True, "note": "On your own network the page opens without a sign-in; through your domain it still asks." if on else "The page asks for a sign-in everywhere, your own network too."})
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
            if p == "/api/restore":
                try: hid = int(body.get("id"))
                except (TypeError, ValueError): return self._json(400, {"error": "which file?"})
                c = db()
                try: code, obj = restore_held(c, hid)
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
