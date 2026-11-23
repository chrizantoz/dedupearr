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
  EXTRA              P1.12: an extra (opening or ending without credits, trailer, sample), a disc image or software in a download folder, with
                     nothing to say otherwise: not library material, so a quiet verdict instead of "to decide"   wasted 0
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
           "qBittorrent": {"url": _env("QBIT_URL").rstrip("/"), "user": _env("QBIT_USER"), "password": _env("QBIT_PASSWORD")},
           "rTorrent": {"url": _env("RTORRENT_URL").rstrip("/"), "user": _env("RTORRENT_USER"), "password": _env("RTORRENT_PASSWORD")},   # P2.3: the XML-RPC address, e.g. http://host:8000/RPC2
           "Transmission": {"url": _env("TRANSMISSION_URL").rstrip("/"), "user": _env("TRANSMISSION_USER"), "password": _env("TRANSMISSION_PASSWORD")},   # http://host:9091 (or the full .../transmission/rpc)
           "SABnzbd": {"url": _env("SABNZBD_URL").rstrip("/"), "key": _env("SABNZBD_APIKEY")}}
USENET = {"NZBGet", "SABnzbd"}   # usenet does not seed: a finished job holds nothing
def is_usenet(ci): return bool(ci) and (ci.get("client") in USENET or ci.get("usenet") is True)
CLIENT_SOURCE = {n: "container" for n, c in CLIENTS.items() if c["url"]}   # where each address came from: container | page | the arr that listed it (P2.0)
DISCOVERED = []   # P2.0: what the arrs' Download Clients pages list, for About and the Setup page
SEED_MIN_RATIO = float(_env("SEED_MIN_RATIO", "1.0") or 1.0); SEED_MIN_DAYS = float(_env("SEED_MIN_DAYS", "14") or 14)
SEED_RULE = _env("SEED_RULE", "auto").lower()   # "auto" (default): private torrents need ratio AND days, public ones either; "both" / "either" for every torrent
CLIENT_PATH_MAP = [tuple(x.split("=", 1)) for x in _env("CLIENT_PATH_MAP").split(",") if "=" in x]   # clientpath=ourpath when a client sees the files under another path
AUDITARR_URL = _env("AUDITARR_URL").rstrip("/")   # P2.4: Auditarr's address; Dedupearr reads which files Auditarr changed and where its trash is
AUDITARR_PATH_MAP = [tuple(x.split("=", 1)) for x in _env("AUDITARR_PATH_MAP").split(",") if "=" in x]   # auditarrpath=ourpath when Auditarr sees the files under another path
AUDITARR_TRASH_NAMES = {".media-audit-trash", ".auditarr-trash"}   # Auditarr's trash folder by name, for when no address is set
AUDITARR_STATUS = {}   # {"ok": bool, "n": int, "note": str} after a scan asked; shown on About
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

ACTIVE = {}   # kind -> the settings the last scan used for it (from here, the page, or an arr's list); the fixes ask the same client
def active_cfg(kind): return ACTIVE.get(kind) or CLIENTS[kind]
def deluge_session(cfg=None):
    """A signed-in call(method, params) for Deluge's web JSON."""
    cfg = cfg or active_cfg("Deluge"); cj = http.cookiejar.CookieJar(); u = cfg["url"] + "/json"; n = [0]
    def call(m, params):
        n[0] += 1; r = http_json(u, {"method": m, "params": params, "id": n[0]}, cj=cj)
        if r.get("error"): raise RuntimeError(f"{m}: {r['error'].get('message') if isinstance(r['error'], dict) else r['error']}")
        return r.get("result")
    if not call("auth.login", [cfg.get("password") or ""]): raise RuntimeError("the web password was refused")
    if not call("web.connected", []):
        hosts = call("web.get_hosts", []) or []
        if hosts: call("web.connect", [hosts[0][0]])
    return call
def read_deluge(cfg=None, timeout=None):
    cfg = cfg or active_cfg("Deluge")
    if not cfg["url"]: return {}
    call = deluge_session(cfg)
    fields = ["name", "save_path", "state", "ratio", "seeding_time", "time_added", "label", "total_size", "is_finished", "progress", "files", "private"]
    ts = call("core.get_torrents_status", [{}, fields]) or {}
    out = {}
    for tid, t in ts.items():
        for f in t.get("files") or []:
            out[map_client_path(os.path.join(t["save_path"], f["path"]))] = {"client": "Deluge", "name": t["name"], "state": str(t.get("state", "")).lower(), "ratio": float(t.get("ratio") or 0),
                                                                            "seeding_days": float(t.get("seeding_time") or 0) / 86400, "label": t.get("label") or "", "finished": bool(t.get("is_finished")), "id": tid,
                                                                            "private": t.get("private") if isinstance(t.get("private"), bool) else None}
    CLIENT_STATUS["Deluge"] = {"ok": True, "n": len(ts), "note": f"{len(ts)} torrents"}; return out

def read_nzbget(cfg=None, timeout=None):
    cfg = cfg or active_cfg("NZBGet")
    if not cfg["url"]: return {}
    H = {"Authorization": "Basic " + base64.b64encode(f"{cfg.get('user') or ''}:{cfg.get('password') or ''}".encode()).decode()}; u = cfg["url"] + "/jsonrpc"
    hist = http_json(u, {"method": "history", "params": [False]}, H, timeout=timeout).get("result") or []
    groups = http_json(u, {"method": "listgroups", "params": [0]}, H, timeout=timeout).get("result") or []
    out = {}
    for h in hist:   # a usenet download is a folder; any file under it belongs to it (keys end with '/')
        d = map_client_path(h.get("FinalDir") or h.get("DestDir") or "")
        if d: out[d.rstrip("/") + "/"] = {"client": "NZBGet", "name": h.get("Name", ""), "state": "finished" if str(h.get("Status", "")).startswith("SUCCESS") else "failed" if "FAILURE" in str(h.get("Status", "")) else str(h.get("Status", "")).lower(),
                                           "ratio": 0.0, "seeding_days": 0.0, "label": h.get("Category") or "", "finished": True, "id": h.get("NZBID"), "usenet": True}
    for g in groups:
        d = map_client_path(g.get("DestDir") or "")
        if d: out[d.rstrip("/") + "/"] = {"client": "NZBGet", "name": g.get("NZBName", ""), "state": "downloading", "ratio": 0.0, "seeding_days": 0.0, "label": g.get("Category") or "", "finished": False, "id": g.get("NZBID"), "usenet": True}
    CLIENT_STATUS["NZBGet"] = {"ok": True, "n": len(hist), "note": f"{len(hist)} in history, {len(groups)} downloading"}; return out

def qbit_session(cfg=None):
    """(base url, cookie jar) of a signed-in qBittorrent session."""
    cfg = cfg or active_cfg("qBittorrent"); cj = http.cookiejar.CookieJar(); base = cfg["url"] + "/api/v2"
    req = urllib.request.Request(base + "/auth/login", data=urllib.parse.urlencode({"username": cfg.get("user") or "", "password": cfg.get("password") or ""}).encode(), headers={"Referer": cfg["url"]})
    if "Ok" not in urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)).open(req, timeout=30).read().decode(): raise RuntimeError("the login was refused")
    return base, cj
def read_qbit(cfg=None, timeout=None):
    cfg = cfg or active_cfg("qBittorrent")
    if not cfg["url"]: return {}
    base, cj = qbit_session(cfg)
    out = {}; ts = http_json(base + "/torrents/info", cj=cj) or []
    for t in ts:
        for f in http_json(base + "/torrents/files?hash=" + t["hash"], cj=cj) or []:
            out[map_client_path(os.path.join(t["save_path"], f["name"]))] = {"client": "qBittorrent", "name": t["name"], "state": str(t.get("state", "")).lower(), "ratio": float(t.get("ratio") or 0),
                                                                             "seeding_days": float(t.get("seeding_time") or 0) / 86400, "label": t.get("category") or "", "finished": float(t.get("progress") or 0) >= 1, "id": t["hash"],
                                                                             "private": next((t[k] for k in ("private", "is_private", "isPrivate") if isinstance(t.get(k), bool)), None)}
    CLIENT_STATUS["qBittorrent"] = {"ok": True, "n": len(ts), "note": f"{len(ts)} torrents"}; return out

def _basic(user, password): return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()} if user or password else {}
def _num(x, d=0.0):
    """A number from a client's answer, or d when it is missing, wounded or not finite."""
    try: v = float(x)
    except (TypeError, ValueError): return d
    return v if v == v and abs(v) != float("inf") else d

def xmlrpc_call(url, method, params, headers=None, timeout=None):
    """One XML-RPC call (rTorrent). Read-only callers only: d.multicall2, f.multicall, system.*. Raises on a fault."""
    import xmlrpc.client
    body = xmlrpc.client.dumps(tuple(params), method).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "text/xml", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout or CLIENT_TIMEOUT) as r: raw = r.read(64 * 1024 * 1024)
    return xmlrpc.client.loads(raw, use_builtin_types=True)[0][0]

RT_FIELDS = ["d.hash=", "d.name=", "d.directory=", "d.custom1=", "d.complete=", "d.ratio=", "d.is_private=", "d.state=", "d.is_active=", "d.timestamp.finished=", "d.left_bytes="]
def read_rtorrent(cfg=None, timeout=None):
    """P2.3: rTorrent over XML-RPC (recorded from rTorrent 0.16 on 2026-11-18). Reads only: one d.multicall2 for the torrents, one system.multicall
    for their files. Ratio comes in thousandths; rTorrent keeps no seeding time, so it is counted from the moment the torrent finished."""
    cfg = cfg or CLIENTS["rTorrent"]
    if not cfg["url"]: return {}
    H = _basic(cfg.get("user"), cfg.get("password")); call = lambda m, p: xmlrpc_call(cfg["url"], m, p, H, timeout)
    ts = [t for t in (call("d.multicall2", ["", "main", *RT_FIELDS]) or []) if isinstance(t, list) and len(t) >= len(RT_FIELDS) and isinstance(t[0], str) and t[0]]
    files = []
    for i in range(0, len(ts), 100):   # every torrent's files in one request per hundred torrents
        res = call("system.multicall", [[{"methodName": "f.multicall", "params": [t[0], "", "f.path="]} for t in ts[i:i + 100]]])
        files += res if isinstance(res, list) else []
    out = {}; now = time.time()
    for t, fr in zip(ts, files, strict=False):   # a short answer leaves the last torrents without files, never shifted
        h, name, d_, label, complete, ratio, private, started, active, fin = t[:10]
        if not isinstance(d_, str) or not d_.startswith("/"): continue
        fl = fr[0] if isinstance(fr, list) and fr and isinstance(fr[0], list) else []
        done = _num(complete) == 1; fin = _num(fin)
        state = ("seeding" if _num(active) == 1 else "paused") if done and _num(started) == 1 else "stopped" if _num(started) != 1 else "downloading"
        info = {"client": "rTorrent", "name": str(name), "state": state, "ratio": _num(ratio) / 1000, "seeding_days": max(0.0, now - fin) / 86400 if done and fin > 0 else 0.0,
                "label": urllib.parse.unquote(label) if isinstance(label, str) else "", "finished": done, "id": h, "private": bool(private) if isinstance(private, (int, bool)) else None}
        for f in fl:
            if isinstance(f, list) and f and isinstance(f[0], str) and f[0]: out[map_client_path(os.path.join(d_, f[0]))] = info
    CLIENT_STATUS["rTorrent"] = {"ok": True, "n": len(ts), "note": f"{len(ts)} torrents"}; return out

TR_STATUS = {0: "stopped", 1: "queued to check", 2: "checking", 3: "queued", 4: "downloading", 5: "queued to seed", 6: "seeding"}
def read_transmission(cfg=None, timeout=None):
    """P2.3: Transmission's RPC (recorded from Transmission 4.1 on 2026-11-18): the session-id handshake, then one torrent-get with the files.
    isFinished in Transmission means 'stopped at its seed limit', so a torrent counts as finished when nothing is left to download."""
    cfg = cfg or CLIENTS["Transmission"]
    if not cfg["url"]: return {}
    u = cfg["url"] if cfg["url"].endswith("/rpc") else cfg["url"] + ("/rpc" if cfg["url"].endswith("/transmission") else "/transmission/rpc")
    H = {"Content-Type": "application/json", **_basic(cfg.get("user"), cfg.get("password"))}; sid = ""
    fields = ["hashString", "name", "downloadDir", "status", "percentDone", "leftUntilDone", "uploadRatio", "secondsSeeding", "isPrivate", "labels", "files"]
    for _ in range(2):
        req = urllib.request.Request(u, data=json.dumps({"method": "torrent-get", "arguments": {"fields": fields}}).encode(), headers={**H, "X-Transmission-Session-Id": sid})
        try:
            with urllib.request.urlopen(req, timeout=timeout or CLIENT_TIMEOUT) as r: j = json.loads(r.read().decode("utf-8", "replace") or "{}"); break
        except urllib.error.HTTPError as e:
            if e.code == 409 and e.headers.get("X-Transmission-Session-Id"): sid = e.headers["X-Transmission-Session-Id"]; continue
            if e.code == 401: raise RuntimeError("the user or password was refused") from None
            raise
    else: raise RuntimeError("it kept asking for a new session")
    if not isinstance(j, dict) or j.get("result") != "success": raise RuntimeError(f"it answered: {str(j.get('result') if isinstance(j, dict) else j)[:80]}")
    ts = [t for t in ((j.get("arguments") or {}).get("torrents") or []) if isinstance(t, dict) and isinstance(t.get("hashString"), str)]
    out = {}
    for t in ts:
        d_ = t.get("downloadDir")
        if not isinstance(d_, str) or not d_.startswith("/"): continue
        done = _num(t.get("percentDone")) >= 1 and _num(t.get("leftUntilDone")) == 0; labels = t.get("labels") if isinstance(t.get("labels"), list) else []
        info = {"client": "Transmission", "name": str(t.get("name") or ""), "state": TR_STATUS.get(t.get("status"), "unknown") if isinstance(t.get("status"), int) else "unknown",
                "ratio": max(0.0, _num(t.get("uploadRatio"))), "seeding_days": max(0.0, _num(t.get("secondsSeeding"))) / 86400, "label": str(labels[0]) if labels else "",
                "finished": done, "id": t["hashString"], "private": t.get("isPrivate") if isinstance(t.get("isPrivate"), bool) else None}
        for f in t.get("files") if isinstance(t.get("files"), list) else []:
            if isinstance(f, dict) and isinstance(f.get("name"), str) and f["name"]: out[map_client_path(os.path.join(d_, f["name"]))] = info
    CLIENT_STATUS["Transmission"] = {"ok": True, "n": len(ts), "note": f"{len(ts)} torrents"}; return out

SAB_DONE = {"completed": "finished", "failed": "failed"}
def read_sabnzbd(cfg=None, timeout=None):
    """P2.3: SABnzbd's API (recorded from SABnzbd 5.1 on 2026-11-18): history (finished and failed jobs, where they were stored) and the queue.
    A finished job's 'storage' is its folder, or the file itself when the job held one file; queue items have no folder yet unless the
    incomplete folder is an absolute path."""
    cfg = cfg or CLIENTS["SABnzbd"]
    if not cfg["url"]: return {}
    api = lambda mode, extra="": http_json(f"{cfg['url']}/api?mode={mode}&output=json&apikey={urllib.parse.quote(cfg.get('key') or '')}{extra}", timeout=timeout)
    h = api("history", "&limit=5000")
    if not isinstance(h, dict) or not isinstance(h.get("history"), dict): raise RuntimeError("the API key was refused" if isinstance(h, dict) and "error" in h else "not SABnzbd's history")
    slots = [s for s in h["history"].get("slots") or [] if isinstance(s, dict)]; out = {}
    for s in slots:
        d_ = s.get("storage") or s.get("path")
        if not isinstance(d_, str) or not d_.startswith("/"): continue
        name = str(s.get("name") or ""); st = str(s.get("status") or "").lower()
        if os.path.splitext(d_)[1].lower() in VIDEO_EXT and os.path.basename(os.path.dirname(d_)) == name: d_ = os.path.dirname(d_)   # one-file job: the job's folder
        key = map_client_path(d_) if os.path.splitext(d_)[1].lower() in VIDEO_EXT else map_client_path(d_).rstrip("/") + "/"
        out[key] = {"client": "SABnzbd", "usenet": True, "name": name, "state": SAB_DONE.get(st, st or "unknown"), "ratio": 0.0, "seeding_days": 0.0, "label": str(s.get("category") or ""), "finished": st in SAB_DONE, "id": s.get("nzo_id")}
    q = api("queue"); qs = [s for s in ((q.get("queue") or {}).get("slots") or []) if isinstance(s, dict)] if isinstance(q, dict) else []
    inc = ""
    if qs:
        try: inc = str((((api("get_config", "&section=misc") or {}).get("config") or {}).get("misc") or {}).get("download_dir") or "")
        except Exception: inc = ""
    for s in qs:
        if inc.startswith("/") and s.get("filename"): out[map_client_path(os.path.join(inc, str(s["filename"]))).rstrip("/") + "/"] = {"client": "SABnzbd", "usenet": True, "name": str(s["filename"]), "state": "downloading", "ratio": 0.0, "seeding_days": 0.0, "label": str(s.get("cat") or ""), "finished": False, "id": s.get("nzo_id")}
    CLIENT_STATUS["SABnzbd"] = {"ok": True, "n": len(slots), "note": f"{len(slots)} in history, {len(qs)} downloading"}; return out

ARR_KINDS = {"deluge": "Deluge", "nzbget": "NZBGet", "qbittorrent": "qBittorrent", "rtorrent": "rTorrent", "transmission": "Transmission", "sabnzbd": "SABnzbd"}
def read_arr_clients():
    """P2.0: each arr's Download Clients page (/api/v3/downloadclient): the kind, its address, whether it is switched on there. The arrs never hand out
    passwords or API keys (they answer '********'), so a client that needs one still needs it set here. -> [{app, kind, known, enabled, url, user, name, priority}]"""
    out = []
    for app, cfg in ARRS.items():
        if not cfg["url"] or not cfg["key"]: continue
        try: lst = http_json(f"{cfg['url']}/api/{api_v(app)}/downloadclient", headers={"X-Api-Key": cfg["key"]})
        except Exception as e: note_error(f"{app} download clients: {e}"); continue
        for dc in lst if isinstance(lst, list) else []:
            if not isinstance(dc, dict): continue
            impl = str(dc.get("implementation") or ""); kind = ARR_KINDS.get(impl.lower())
            f = {x.get("name"): x.get("value") for x in (dc.get("fields") if isinstance(dc.get("fields"), list) else []) if isinstance(x, dict)}
            host = str(f.get("host") or "").strip(); port = f.get("port"); base = str(f.get("urlBase") or "").strip()
            url = ""
            if host and re.fullmatch(r"[A-Za-z0-9._:\[\]-]+", host) and isinstance(port, int) and 0 < port < 65536:
                url = f"http{'s' if f.get('useSsl') is True else ''}://{host}:{port}" + (("/" + base.strip("/")) if base.strip("/") else "")
                if kind == "rTorrent" and not base.strip("/"): url += "/RPC2"
                if kind == "Transmission": url = url.rstrip("/") + ("/rpc" if url.rstrip("/").endswith("/transmission") else "")
            out.append({"app": app, "kind": kind or (str(dc.get("implementationName") or impl) or "unknown"), "known": bool(kind), "enabled": dc.get("enable") is True, "url": url,
                        "user": str(f.get("username") or "") if isinstance(f.get("username"), str) else "", "name": str(dc.get("name") or "")[:80], "priority": dc.get("priority") if isinstance(dc.get("priority"), int) else 99})
    return out

def plan_clients(found, configured):
    """Which clients a scan asks, from the addresses set here and what the arrs list. -> (read, soft, notes)
    read: {kind: (cfg, source)} asked and reported when they fail. soft: {kind: (cfg, source)} only listed switched-off in the arrs: asked once,
    briefly; used when they answer, never reported as broken when they do not (a switched-off client is usually not running).
    An address set here always wins; one address per kind (the first switched-on one, by the arr's priority)."""
    read, soft = {}, {}
    for kind, cfg in configured.items():
        if cfg.get("url"): read[kind] = (cfg, CLIENT_SOURCE.get(kind, "container"))
    for f in sorted(found, key=lambda f: (not f["enabled"], f["priority"])):
        k = f["kind"]
        if not f["known"] or not f["url"] or k in read or (k in soft and not f["enabled"]): continue
        cfg = dict(configured.get(k) or {}, url=f["url"])
        if f["user"] and not cfg.get("user"): cfg["user"] = f["user"]
        (read if f["enabled"] else soft)[k] = (cfg, f["app"])
        if f["enabled"]: soft.pop(k, None)
    notes = [f"{f['app'].capitalize()} also uses {f['kind']}, which Dedupearr cannot read yet." for f in found if not f["known"]]   # a switched-off one gets its note after it was asked
    return read, soft, list(dict.fromkeys(notes))

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
    out = {}; LAYOUT.clear()
    for app, cfg in ARRS.items():
        if not cfg["url"] or not cfg["key"]: continue
        H = {"X-Api-Key": cfg["key"]}; n = 0; page = 1; tops = {}
        try:
            while page <= 40:
                r = http_json(f"{cfg['url']}/api/{api_v(app)}/history?page={page}&pageSize=500&eventType=3&sortKey=date&sortDirection=descending", headers=H)
                recs = r.get("records") or []
                for rec in recs:
                    d = rec.get("data") or {}; dropped = map_client_path(d.get("droppedPath") or "")
                    if dropped and dropped not in out: out[dropped] = {"app": app, "imported": d.get("importedPath") or "", "when": str(rec.get("date", ""))[:10], "title": rec.get("sourceTitle", "")}
                    if isinstance(d.get("droppedPath"), str) and isinstance(d.get("importedPath"), str):   # P4.1: the first folder of each side, as the arr sees them
                        k = (_top(d["droppedPath"]), _top(d["importedPath"])); tops[k] = tops.get(k, 0) + 1
                n += len(recs)
                if len(recs) < 500: break
                page += 1
            CLIENT_STATUS[app.capitalize()] = {"ok": True, "n": n, "note": f"{n} imports in its history"}
            if tops: (dl, lib), k = max(tops.items(), key=lambda kv: kv[1]); LAYOUT[app] = {"from": dl, "to": lib, "n": k, "all": sum(tops.values())}
        except Exception as e: CLIENT_STATUS[app.capitalize()] = {"ok": False, "n": 0, "note": str(e)[:160]}; note_error(f"{app}: {e}")
    return out

LAYOUT = {}   # P4.1: app -> the folders it imports from and to, from its own history (the shared-mount test reads it)
def _top(p):
    parts = [x for x in str(p).replace("\\", "/").split("/") if x]
    return "/" + parts[0] if parts and str(p).startswith("/") else (parts[0] if parts else "")
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

def map_auditarr_path(p):
    for a, b in AUDITARR_PATH_MAP:
        if p.startswith(a): return b.rstrip("/") + p[len(a.rstrip("/")):]
    return p
def read_auditarr():
    """Auditarr's own list (its /api/changed): the files it changed, with when and what in plain words, and where its trash is.
    -> {"files": {path: (ts, what)}, "trash": [folder]}. An Auditarr that does not answer leaves both empty and is reported on About."""
    out = {"files": {}, "trash": []}
    if not AUDITARR_URL: AUDITARR_STATUS.clear(); return out
    try:
        j = http_json(AUDITARR_URL + "/api/changed")
        if not isinstance(j, dict) or not isinstance(j.get("files"), list): raise ValueError("not Auditarr's list of changed files (an older Auditarr, or a sign-in page)")
        for row in j["files"]:
            if isinstance(row, list) and len(row) == 3 and isinstance(row[0], str): out["files"][map_auditarr_path(row[0])] = (float(row[1] or 0), re.sub(r"[^\w ,.'-]", " ", str(row[2]))[:80])
        if isinstance(j.get("trash_dir"), str) and j["trash_dir"].startswith("/"): out["trash"] = [map_auditarr_path(j["trash_dir"]).rstrip("/")]
        AUDITARR_STATUS.clear(); AUDITARR_STATUS.update(ok=True, n=len(out["files"]), note=f"{len(out['files'])} files it changed are known")
    except Exception as e:
        AUDITARR_STATUS.clear(); AUDITARR_STATUS.update(ok=False, n=0, note=str(e)[:160]); note_error(f"Auditarr: {e}")
    return out
def in_auditarr_trash(path, trash=()):
    return any(path.startswith(t + "/") for t in trash) or any(part in AUDITARR_TRASH_NAMES for part in path.split("/")[:-1])
def auditarr_status():
    if AUDITARR_STATUS or not AUDITARR_URL: return AUDITARR_STATUS
    try:
        c = db(); saved = kv_get(c, "auditarr_status"); c.close()
        if saved: AUDITARR_STATUS.update(json.loads(saved))
    except Exception: pass
    return AUDITARR_STATUS

def gather_context():
    """Ask every configured client and arr once per scan. A client that fails is reported on About and simply leaves its files unexplained."""
    clients = {}; found = read_arr_clients(); read, soft, notes = plan_clients(found, CLIENTS)
    DISCOVERED.clear(); DISCOVERED.extend(found); ACTIVE.clear()
    for name, fn in READERS.items():
        if name not in read and name not in soft: CLIENT_STATUS.pop(name, None); continue
        cfg, src = read.get(name) or soft[name]; ACTIVE[name] = cfg
        try: clients.update(fn(cfg, None if name in read else min(CLIENT_TIMEOUT, 10)))
        except Exception as e:
            if name in soft:   # switched off in the arr and not answering: that is what switched off usually means, not a fault
                CLIENT_STATUS.pop(name, None); ACTIVE.pop(name, None); notes.append(f"{name} is switched off in {src.capitalize()} and did not answer at its address, so it is not read."); continue
            hint = f" (its address came from {src.capitalize()}'s settings, which never share passwords or API keys: set them here)" if src in ARRS and name in NEEDS_SECRET else ""
            CLIENT_STATUS[name] = {"ok": False, "n": 0, "note": (str(e)[:160] + hint)[:300]}; note_error(f"{name}: {e}")
            continue
        CLIENT_STATUS[name]["source"] = src
        if name in soft: CLIENT_STATUS[name]["note"] += f"; switched off in {src.capitalize()}, but still running, so its files are explained"
    DISCOVERY_NOTES[:] = notes
    ctx = {"clients": clients, "imports": read_arr_imports(), "queue": read_arr_queue(), **read_arr_roots(), "clients_ok": any(v["ok"] for k, v in CLIENT_STATUS.items() if k in CLIENTS), "auditarr": read_auditarr()}
    try:   # remembered across restarts, so the page and About stay consistent until the next scan asks again
        c = db(); kv_set(c, "client_status", json.dumps(CLIENT_STATUS)); kv_set(c, "auditarr_status", json.dumps(AUDITARR_STATUS))
        kv_set(c, "discovery", json.dumps({"notes": notes, "found": [{k: v for k, v in f.items() if k != "user"} for f in found]})); kv_set(c, "arr_layout", json.dumps(LAYOUT)); c.close()
    except Exception as e: log(f"could not remember the client status: {e}")
    return ctx

READERS = {"Deluge": read_deluge, "NZBGet": read_nzbget, "qBittorrent": read_qbit, "rTorrent": read_rtorrent, "Transmission": read_transmission, "SABnzbd": read_sabnzbd}
NEEDS_SECRET = {"Deluge", "NZBGet", "qBittorrent", "SABnzbd"}   # these never answer without the password or key the arrs keep to themselves
DISCOVERY_NOTES = []   # plain sentences about clients the arrs list that are not read (switched off, or a kind Dedupearr cannot read)

def discovery():
    """What the arrs list and the notes about it, from this run or remembered from the last scan. -> {"notes": [...], "found": [...]}"""
    if DISCOVERED or DISCOVERY_NOTES: return {"notes": list(DISCOVERY_NOTES), "found": [{k: v for k, v in f.items() if k != "user"} for f in DISCOVERED]}
    try:
        c = db(); saved = kv_get(c, "discovery"); c.close(); j = json.loads(saved) if saved else {}
        return {"notes": [str(n) for n in j.get("notes") or []], "found": [f for f in j.get("found") or [] if isinstance(f, dict)]}
    except Exception: return {"notes": [], "found": []}
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
    if is_usenet(ci): return True
    r_ok, d_ok = ci.get("ratio", 0) >= SEED_MIN_RATIO, ci.get("seeding_days", 0) >= SEED_MIN_DAYS
    return (r_ok or d_ok) if rule_for(ci) == "either" else (r_ok and d_ok)

def rule_for(ci):
    """The seeding rule for this torrent: 'both' or 'either'. With SEED_RULE=auto (2026-11-13, owner: "some are private but most are public")
    a private torrent needs ratio AND days; a public one either; a client that does not say is treated as private (the safe side)."""
    if SEED_RULE in ("both", "either"): return SEED_RULE
    return "either" if (ci or {}).get("private") is False else "both"

def seed_wait(ci):
    """Short words for the row while a torrent still seeds: when it can go. '' when it can go now or nothing seeds it."""
    if not ci or is_usenet(ci) or seeding_done(ci): return ""
    if not ci.get("finished"): return "Still downloading"
    days_left = max(0.0, SEED_MIN_DAYS - (ci.get("seeding_days") or 0)); ratio_left = (ci.get("ratio") or 0) < SEED_MIN_RATIO
    d_txt = "tomorrow" if days_left <= 1 else f"in about {round(days_left)} days"
    if rule_for(ci) == "either": return f"Still seeding · can go {d_txt}" + (f", or sooner at ratio {SEED_MIN_RATIO:g}" if ratio_left else "")
    if days_left <= 0: return f"Still seeding (private) · can go once ratio reaches {SEED_MIN_RATIO:g}"
    return f"Still seeding{' (private)' if ci.get('private') else ''} · can go {d_txt}" + (f" if ratio has reached {SEED_MIN_RATIO:g}" if ratio_left else "")

def client_text(ci):
    if ci is None: return "not in any download client"
    if is_usenet(ci): return f"{ci['client']}: {ci['state']}" + (f" ({ci['label']})" if ci.get("label") else "")
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
    if "link_ino" not in {r[1] for r in c.execute("PRAGMA table_info(held)")}: c.execute("ALTER TABLE held ADD COLUMN link_ino INTEGER")   # P3.3: the hardlink a relink left at the old place
    cols = {r[1] for r in c.execute("PRAGMA table_info(verdict)")}
    for col, typ in (("client", "TEXT"), ("removable", "INTEGER"), ("category", "TEXT"), ("queue", "TEXT"), ("auditarr", "TEXT")):
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

_EXTRA_NAME = re.compile(r"(?i)(?<![a-z])(nc ?op|nc ?ed|creditless|clean ?(opening|ending)|trailers?|teasers?|samples?|featurettes?|behind[ ._-]the[ ._-]scenes|deleted[ ._-]scenes?|making[ ._-]of|bd[ ._-]?menus?)(?![a-z])")
_EXTRA_DIRS = {"extras", "extra", "featurettes", "trailers", "samples", "sample", "behind the scenes", "deleted scenes", "interviews", "bonus", "creditless", "menus", "nc"}
_SOFTWARE_DIRS = {"software", "apps", "applications", "programs", "games", "pc games", "roms"}
def extra_kind(path):
    """P1.12: what a download is when it is not library material, in a few words; '' when it looks like an episode or a film.
    'Show - NCOP 01 [BD].mkv' -> 'an opening or ending without credits'; 'x.iso' -> 'a disc image'; a file under 'software/' -> 'software or a game'."""
    name = os.path.basename(path); segs = [x.lower() for x in path.split("/")[:-1]]
    m = _EXTRA_NAME.search(os.path.splitext(name)[0])
    if m and re.match(r"(?i)nc|creditless|clean", m.group(1)): return "an opening or ending without credits"
    if os.path.splitext(name)[1].lower() == ".iso": return "a disc image"
    if any(x in _SOFTWARE_DIRS for x in segs): return "software or a game"
    if m and re.search(r"(?i)\bs\d{1,2}[ ._-]?e\d{1,3}\b", name): m = None   # an episode whose title says 'Trailer' or 'Free Samples' is an episode
    if m or any(x in _EXTRA_DIRS for x in segs): return "an extra (a trailer, sample, menu or bonus clip)"
    return ""

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
    if FIXING["path"]: log("scan skipped: a fix is still comparing two big files"); return None
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
    aud = ctx.get("auditarr") or {"files": {}, "trash": []}; atrash = set(); extra_a = {}   # P2.4: what Auditarr changed, and its trash (a recycle bin Auditarr empties itself)
    for r in rows:
        if in_auditarr_trash(r["path"], aud["trash"]):
            atrash.add(r["path"])
            if r["kind"] != "recycle": r["kind"] = "recycle"; c.execute("UPDATE file SET kind='recycle' WHERE path=?", (r["path"],))
    def by_auditarr(path):
        a = aud["files"].get(path)
        return {"what": a[1], "when": datetime.datetime.fromtimestamp(a[0]).strftime("%Y-%m-%d")} if a else None
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
        candidates = sorted((l for l in lib_by_size.get(r["size"], []) if (l["dev"], l["ino"]) != (r["dev"], r["ino"])), key=lambda l: l["path"])   # by name, so the answer never depends on the order the disk lists folders
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
                    if (a := by_auditarr(l["path"])): extra_a[r["path"]] = a; ev += f"; Auditarr changed the library copy on {a['when']} (it {a['what']})"
                    break
                code, twin, ev = "LOOKALIKE", l["path"], f"same size as {friendly_title(l['path'])} ({human(r['size'])}) but the content differs"
        if code == "DOWNLOAD_UNMATCHED":   # nothing identical, but is there a different release of the same title?
            k = title_key(r["path"]) or title_key(os.path.dirname(r["path"]) + ".x")
            for k2, l in lib_by_title.get((k or " ").rsplit(" ", 1)[0], []):
                if same_title(k, k2):
                    code, twin, ev = "DOWNLOAD_OTHER_RELEASE", l["path"], f"inode {r['ino']}, link count {r['nlink']}, {human(r['size'])}; the library has a different release of the same title ({human(l['size'])}, inode {l['ino']})"; break
        ci = client_for(r["path"], clients); ai = imports.get(r["path"])
        if any(m["path"] in atrash for m in groups.get((r["dev"], r["ino"]), [])): ev += "; the library copy it was linked to now sits in Auditarr's trash"
        if code in ("DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LOOKALIKE"):   # a lookalike is only a size coincidence: the client and the arr say what the file really is
            if ai and ai.get("imported"):
                gone = not os.path.exists(ai["imported"])
                if code == "LOOKALIKE": twin, ev = None, f"inode {r['ino']}, link count {r['nlink']}, {human(r['size'])}"   # a lookalike is a size coincidence: the arr says which library file this was, never a stranger of the same size
                code, twin = "LEFTOVER", twin or ai["imported"]
                ev += f"; {ai['app'].capitalize()} imported it on {ai['when']} to {ai['imported']}" + (", and that file has since been replaced" if gone else ", which has since been rewritten (different content now)")
                if not gone and (a := by_auditarr(ai["imported"])): extra_a[r["path"]] = a; twin = ai["imported"]; ev += f"; Auditarr rewrote it on {a['when']} (it {a['what']})"
            elif (aq := queue_for(r["path"], queue)) and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE"):   # #20: finished, and the arr is about to import it (or is stuck)
                code, twin = "AWAITING_IMPORT", None; ev += f"; {aq['app'].capitalize()} is {IMPORT_WAIT[aq['state']]}" + (f": {aq['why']}" if aq["why"] else ""); extra_q[r["path"]] = aq
            elif ci and not ci.get("finished") and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE"):   # #20: still coming in; neither seeding nor waste yet
                code, twin = "DOWNLOADING", None; ev += "; still downloading"
            elif clients_ok and ci is None and any(a["url"] for a in ARRS.values()): code = "ORPHAN"; ev += "; no download client knows this file and no arr imported it"
            elif is_usenet(ci) and ci.get("finished") and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE") and any(a["url"] for a in ARRS.values()):   # a finished (or failed) usenet job nobody imported holds space for nothing: to decide, never "seeding only"
                if code == "LOOKALIKE": code, twin, ev = "DOWNLOAD_UNMATCHED", None, f"inode {r['ino']}, link count {r['nlink']}, same size as a library file but different content"
                ev += "; no arr ever imported it, and usenet does not seed"
            elif ci and code in ("DOWNLOAD_UNMATCHED", "LOOKALIKE") and any(a["url"] for a in ARRS.values()): code = "SEEDING_ONLY"; ev += "; no arr ever imported it"
            if code == "DOWNLOAD_UNMATCHED" and not (is_usenet(ci) and ci.get("finished")) and (xk := extra_kind(r["path"])):   # P1.12: not library material; a finished usenet job stays a decision (it holds space for nothing)
                code = "EXTRA"; ev += f"; the name says it is {xk}, not something an arr imports"
        if ci or clients_ok: ev += "; " + client_text(ci)
        extra[r["path"]] = (ci, seeding_done(ci))
        verdicts[r["path"]] = (code, "download", r["size"], r["size"] if code in ("DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN") else 0, twin, ev)
    # 3. everything else
    for r in rows:
        if r["path"] in verdicts: continue
        if r["kind"] == "library": verdicts[r["path"]] = ("LIBRARY_ONLY", "library", r["size"], 0, None, f"inode {r['ino']}, link count {r['nlink']}")
        elif r["path"] in atrash: verdicts[r["path"]] = ("AUDITARR_BIN", "recycle", r["size"], 0, None, "inside Auditarr's trash folder; Auditarr deletes it for good after its own keep time, or puts it back")
        elif r["kind"] == "recycle":
            days = int((now - r["mtime"]) // 86400)
            old = days > RECYCLE_DAYS
            verdicts[r["path"]] = ("BIN_OLD" if old else "BIN_RECENT", "recycle", r["size"], r["size"] if old else 0, None, f"in the bin for {days} days, rule is {RECYCLE_DAYS}")
        else: verdicts[r["path"]] = ("OTHER", "other", r["size"], 0, None, "folder name gives no hint whether this is library, downloads or a recycle bin; add it to LIBRARY_DIRS, DOWNLOAD_DIRS or RECYCLE_DIRS")
    # write back, keeping first_seen
    seen = set(verdicts)
    for path, (code, kind, size, wasted, twin, ev) in verdicts.items():
        ci, rem = extra.get(path, (None, True)); cat = category_of(path, ctx, twin if code in TWIN_IS_EVIDENCE else None, ci, imports.get(path))
        c.execute("""INSERT INTO verdict(path,code,title,kind,size,wasted,twin,evidence,first_seen,last_seen,client,removable,category,queue,auditarr) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(path) DO UPDATE SET code=excluded.code,title=excluded.title,kind=excluded.kind,size=excluded.size,wasted=excluded.wasted,
                     twin=excluded.twin,evidence=excluded.evidence,last_seen=excluded.last_seen,client=excluded.client,removable=excluded.removable,category=excluded.category,queue=excluded.queue,auditarr=excluded.auditarr""",
                  (path, code, friendly_title(path), kind, size, wasted, twin, ev, now, now, json.dumps(ci) if ci else None, 1 if rem else 0, cat, json.dumps(extra_q[path]) if path in extra_q else None, json.dumps(extra_a[path]) if path in extra_a else None))
    for r in c.execute("SELECT path FROM verdict").fetchall():
        if r["path"] not in seen: c.execute("DELETE FROM verdict WHERE path=?", (r["path"],))
    c.commit()
    return sum(v[3] for v in verdicts.values())

# ---------------------------------------------------------------------------------------------------------- plain words
LABEL = {"REAL_COPY": "Real copy", "LIBRARY_ONLY": "Only copy", "LIBRARY_TWIN": "Two names, one file", "LINKED_SEED": "Seeding name",
         "DUPLICATE_COPY": "Stored twice", "DUPLICATE_HEADER": "Stored twice, header differs", "LOOKALIKE": "Looks alike", "DOWNLOAD_UNMATCHED": "Download without a match", "DOWNLOAD_OTHER_RELEASE": "Different release in library",
         "LEFTOVER": "Leftover from an upgrade", "ORPHAN": "Orphan", "SEEDING_ONLY": "Seeding only", "DOWNLOADING": "Still downloading", "AWAITING_IMPORT": "Waiting for import",
         "EXTRA": "Extra, not library",
         "BIN_OLD": "Old recycle-bin file", "BIN_RECENT": "In the recycle bin", "AUDITARR_BIN": "In Auditarr's trash", "OTHER": "Unsorted folder"}
NEEDS_YOU = {"DUPLICATE_COPY", "DUPLICATE_HEADER", "DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN", "BIN_OLD", "OTHER"}
def seed_sentence(ci):
    if ci is None: return "Nothing holds it, so it can go now."
    if is_usenet(ci): return "It came from usenet, so there is no seeding to wait for; it can go now."
    if seeding_done(ci): return f"{ci['client']} has seeded it to ratio {ci['ratio']:.1f} over {ci['seeding_days']:.0f} days, past your rule, so it can go now."
    kind = "" if SEED_RULE in ("both", "either") else (" (a private torrent)" if ci.get("private") else " (a public torrent)" if ci.get("private") is False else " (the client does not say whether it is private, so the safe rule)")
    return f"{ci['client']} is still seeding it (ratio {ci['ratio']:.2f}, {ci['seeding_days']:.0f} days); your rule{kind} keeps it until ratio {SEED_MIN_RATIO:g} {'or' if rule_for(ci) == 'either' else 'and'} {SEED_MIN_DAYS:g} days."
def sentence(v):
    code, size, twin = v["code"], human(v["size"]), v.get("twin")
    t = friendly_title(twin) if twin else ""
    ci = v.get("client") if isinstance(v.get("client"), dict) else (json.loads(v["client"]) if v.get("client") else None)
    tail = (" " + seed_sentence(ci)) if (ci or v.get("clients_known")) else ""
    au = v.get("auditarr") if isinstance(v.get("auditarr"), dict) else (json.loads(v["auditarr"]) if v.get("auditarr") else None)
    if code == "AUDITARR_BIN": return "In Auditarr's trash. Auditarr deletes it for good when its own keep time runs out, or puts it back if a re-download fails. Dedupearr leaves it alone and does not count it as library."
    if code == "LEFTOVER" and au: return f"A leftover: this download was imported into the library, and Auditarr has since rewritten the library file {t} (it {au['what']} on {au['when']}). The library keeps the rewritten file, so this download is only still needed for seeding. {size} to get back.{tail}"
    if code == "DUPLICATE_HEADER" and au: return f"Stored twice: the library file {t} and this download are the same video, except the first part of the file differs, because Auditarr changed the library copy (it {au['what']} on {au['when']}). Relinking would not be safe, because the torrent would put the old header back; the download is only still needed for seeding and can go once that is done. {size} to get back.{tail}"
    if code == "LEFTOVER": return f"A leftover from an upgrade: this download was imported into the library and the library has since moved on ({t} is what it keeps now). {size} to get back.{tail}"
    if code == "ORPHAN": return f"An orphan: no download client knows this file and no arr imported it. {size} to get back. Nothing holds it, so it can go now."
    if code == "DOWNLOADING": return f"Still downloading in {ci['client'] if ci else 'your download client'}. Nothing to judge yet; it is looked at again once it finishes."
    if code == "AWAITING_IMPORT":
        aq = v.get("queue") if isinstance(v.get("queue"), dict) else (json.loads(v["queue"]) if v.get("queue") else {}); app = (aq.get("app") or "your arr").capitalize()
        if aq.get("state") == "importBlocked": return f"Finished, but {app} cannot import it on its own" + (f" ({aq['why'].rstrip('.')})" if aq.get("why") else "") + f". Open {app}'s Activity queue and import it by hand or remove it there; Dedupearr leaves it alone."
        return f"Finished, and {app} is {IMPORT_WAIT.get(aq.get('state'), 'about to import it')}. Nothing to do; once it is in the library this download is judged like any other."
    if code == "SEEDING_ONLY": return f"Kept for seeding: {ci['client'] if ci else 'your client'} is seeding it and nothing in the library came from it" + (f" (it is {xk})" if (xk := extra_kind(v.get('path') or '')) else "") + ". Not counted as waste; it goes when you remove the torrent."
    if code == "EXTRA": return f"Not library material: the name says it is {extra_kind(v.get('path') or '') or 'an extra'}. The arrs never import such files, so it is neither waste nor something to decide. Keep it, or remove it in your download client when you no longer want it."
    if code == "REAL_COPY": return "One file with two names: the library and the download folder share it, so seeding costs no extra space."
    if code == "LINKED_SEED": return f"This is the download-folder name of the library file {t}. Same file, no extra space."
    if code == "LIBRARY_ONLY": return "One copy, in the library. Nothing to do."
    if code == "LIBRARY_TWIN": return f"Two names inside the library share one file ({t} is the other). No space wasted."
    if code == "DUPLICATE_COPY": return f"Stored twice: the library file {t} and this download are two separate files with identical content. Making them one file again would free {size}.{tail}"
    if code == "DUPLICATE_HEADER": return f"Stored twice: the library file {t} and this download are the same video, except the first part of the file differs. That usually means a tag or title was edited on one copy (Auditarr does this when it fixes audio languages). Relinking would not be safe, because the torrent would put the old header back; once seeding is done the download can go. {size} to get back.{tail}"
    if code == "LOOKALIKE": return f"Same size as {t} but different content, so not a duplicate. Worth a look if you expected one copy."
    if code == "DOWNLOAD_UNMATCHED" and is_usenet(ci) and ci.get("finished"):
        how = "marked it failed" if ci.get("state") == "failed" else "finished it" if ci.get("state") == "finished" else "lists it as " + str(ci.get("state") or "done").replace("/", ", ")
        return f"A usenet download nothing in the library matches and no arr imported: {ci['client']} {how}. Usenet does not seed, so nothing holds it; import it if you want it, otherwise it can go. {size} to decide on."
    if code == "DOWNLOAD_UNMATCHED": return f"Nothing in the library matches this download. It may be a leftover from an upgrade, an orphan, or something still seeding; the download-client check (phase 2) will tell. {size} to decide on."
    if code == "DOWNLOAD_OTHER_RELEASE": return f"Nothing identical in the library, but it holds a different release of the same title: {t}. If that one is the keeper, this download is a leftover from an upgrade and can go once seeding is done. {size} to get back.{tail}"
    if code == "BIN_OLD": return f"In a recycle bin longer than your {RECYCLE_DAYS}-day rule. Emptying it would free {size}."
    if code == "BIN_RECENT": return f"In a recycle bin, within your {RECYCLE_DAYS}-day rule. It will show up here when the rule runs out."
    return "Not inside a folder I recognise as library, downloads or a recycle bin. Name the folder in the settings and it will be judged on the next scan."

# -------------------------------------------------------------------------------------------------------------- fixes (phase 3)
FIXABLE = {"DUPLICATE_COPY": "relink", "DUPLICATE_HEADER": "remove", "DOWNLOAD_OTHER_RELEASE": "remove", "LEFTOVER": "remove", "ORPHAN": "bin", "BIN_OLD": "empty"}
def no_remove_call(ci):
    """A torrent in a client Dedupearr can read but cannot tell to forget a torrent (rTorrent, Transmission): moving its file would break the torrent."""
    return bool(ci) and not is_usenet(ci) and ci.get("client") not in REMOVE_READERS
def no_remove_sentence(ci):
    return f"Dedupearr can read {ci['client']} but cannot remove torrents from it yet, so this file stays where {ci['client']} expects it. Remove the torrent in {ci['client']} (keep its files) and the next scan offers the move."
def plan_fix(v):
    """The exact steps a fix would take for this file, in plain words, before anything is touched. Returns (kind, steps, frees) or (None, [reason], 0)."""
    code = v["code"]; ci = v.get("client") if isinstance(v.get("client"), dict) else (json.loads(v["client"]) if v.get("client") else None)
    kind = FIXABLE.get(code)
    if not kind: return None, ["Nothing to fix here: this file is fine or needs no change."], 0
    if kind == "relink" and ci and not ci.get("finished"): return None, ["The client is still downloading this file, so it is left alone."], 0   # a relink keeps the client seeding, so it does not wait for the seeding rule
    if not v.get("removable", True) and kind == "remove": return None, [seed_sentence(ci)], 0
    if kind == "remove" and no_remove_call(ci): return None, [no_remove_sentence(ci)], 0   # P2.3: a torrent this app cannot remove keeps its file where the client expects it
    hold = f"{HOLDING_DIR}/{os.path.basename(v['path'])}"
    if kind == "relink": steps = ["Compare both files byte for byte right before the change (a big file takes a few minutes).", f"Move this copy into the holding bin at {hold} (nothing is deleted).", f"Create a hardlink from the library file {friendly_title(v['twin'] or '')} at this download's name, so the client keeps seeding the same bytes.",
                                 "Confirm both names are now one file; if anything fails, put the copy back.", f"Keep the moved copy for {HOLD_DAYS} days, then delete it, unless you press Undo."]
    elif kind == "remove": steps = ([f"Ask {ci['client']} once more whether the torrent '{ci['name']}' is still past your seeding rule, then remove it there without deleting its files." if ci and not is_usenet(ci) else (f"It came from usenet and nothing seeds it; {ci['client']}'s history is left as it is." if ci else "No client holds this file, so there is nothing to tell a client.")]
                                    + [f"Move the file into the holding bin at {hold} (nothing is deleted).", f"Keep it for {HOLD_DAYS} days, then delete it, unless you press Undo."])
    elif kind == "bin": steps = [f"Move the file into the holding bin at {hold} (nothing is deleted).", f"Keep it for {HOLD_DAYS} days, then delete it, unless you press Undo."]
    else: steps = [f"The file has sat in its recycle bin longer than your {RECYCLE_DAYS}-day rule.", f"Move it into the holding bin at {hold} (nothing is deleted yet).", f"Keep it for {HOLD_DAYS} days, then delete it, unless you press Undo."]   # P3.4: no fix deletes directly, not even from a recycle bin
    return kind, steps, v.get("wasted") or 0

def record_action(c, mode, kind, path, detail, result, undo=None):
    c.execute("INSERT INTO action(ts,mode,kind,path,detail,result,undo) VALUES(?,?,?,?,?,?,?)", (time.time(), mode, kind, path, detail[:500], result[:500], json.dumps(undo) if undo else None)); c.commit()

# ------------------------------------------------------------------- the lock shared with Auditarr (P3.5): one marker per file, in a folder both apps can see
LOCK_DIR = _env("LOCK_DIR") or (SCAN_ROOTS[0].rstrip("/") + "/.fix-locks"); LOCK_STALE_H = float(_env("LOCK_STALE_H", "6") or 6); LOCK_APP = "Dedupearr"
_LOCKS = {}; _LOCKS_GUARD = threading.Lock()   # marker -> how many times this app holds it right now
class FileBusy(RuntimeError): pass
def _lock_marker(path):
    """The marker is named after the file's number on the disk (its inode), not its name: two names of one file share one lock, and the two
    apps need not see the media under the same folder names."""
    try: return os.path.join(LOCK_DIR, f"{os.stat(path).st_ino}.lock")
    except (OSError, TypeError): return None
def _lock_read(marker):
    """(what the marker says, its age in seconds), or (None, 0) when there is no marker. A marker still being written reads as {}."""
    try: age = time.time() - os.stat(marker).st_mtime
    except OSError: return None, 0
    try:
        with open(marker) as fh: info = json.load(fh)
    except (OSError, ValueError): info = {}
    return (info if isinstance(info, dict) else {}), age
def _lock_theirs(info, age): return info is not None and age <= LOCK_STALE_H * 3600 and info.get("app") != LOCK_APP
def _lock_sentence(info): return f"{info.get('app') or 'Another app'} is working on this file right now ({info.get('what') or 'changing it'}), so nothing was changed; try again in a few minutes"
def lock_holder(path):
    """A sentence naming the other app that is changing this file right now, or '' when nobody is."""
    marker = _lock_marker(path)
    if not marker: return ""
    info, age = _lock_read(marker)
    return _lock_sentence(info) if _lock_theirs(info, age) else ""
def lock_take(path, what):
    """Mark the file as being changed by this app. Returns a token for lock_drop; raises FileBusy when the other app holds it.
    No lock folder (or a read-only one) means no lock: the fix runs as it did before."""
    marker = _lock_marker(path)
    if not marker: return None
    with _LOCKS_GUARD:
        if _LOCKS.get(marker): _LOCKS[marker] += 1; return marker
        for _ in (0, 1):
            try:
                if not os.path.isdir(LOCK_DIR): os.mkdir(LOCK_DIR)
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                info, age = _lock_read(marker)
                if _lock_theirs(info, age): raise FileBusy(_lock_sentence(info)) from None
                try: os.remove(marker)   # older than LOCK_STALE_H hours, or left behind by this app before a restart
                except OSError: pass
                continue
            except OSError: return None
            with os.fdopen(fd, "w") as fh: json.dump(dict(app=LOCK_APP, what=what, path=path, since=time.time()), fh)
            _LOCKS[marker] = 1; return marker
    raise FileBusy("Another app took this file at the same moment, so nothing was changed; try again in a minute")
def lock_drop(marker):
    if not marker: return
    with _LOCKS_GUARD:
        n = _LOCKS.get(marker, 0) - 1
        if n > 0: _LOCKS[marker] = n; return
        _LOCKS.pop(marker, None)
        try: os.remove(marker)
        except OSError: pass
def lock_sweep():
    """At start-up: markers this app left behind when it was stopped mean nothing any more."""
    try: names = os.listdir(LOCK_DIR)
    except OSError: return 0
    n = 0
    for name in names:
        m = os.path.join(LOCK_DIR, name); info, age = _lock_read(m)
        if name.endswith(".lock") and info is not None and (info.get("app") == LOCK_APP or age > LOCK_STALE_H * 3600):
            try: os.remove(m); n += 1
            except OSError: pass
    return n
# ------------------------------------------------------------------------------------------------------- holding bin (P3.1)
READ_ONLY_NOTE = "Dedupearr can only read your media folder (it is mounted read-only), so nothing was moved. To let fixes move files, mount the share read-write; dry runs work either way."
def hold_target(path, now):
    """Where a file waits in the holding bin: HOLDING_DIR/<day>/<its full path>, so two files with one name never collide and the way back is plain to see."""
    return os.path.join(HOLDING_DIR, time.strftime("%Y-%m-%d", time.localtime(now)), os.path.normpath(path).lstrip(os.sep))
def under_roots(path):
    n = os.path.normpath(path); return any(n.startswith(os.path.normpath(r) + os.sep) for r in SCAN_ROOTS) and not n.startswith(os.path.normpath(HOLDING_DIR) + os.sep)
def hold_file(c, path, kind, why, now=None):
    """Move one file into the holding bin after checking it is still the file the scan judged. Returns (code, obj). Nothing is ever deleted here.
    Refused while Auditarr is changing the same file."""
    if not under_roots(path): return 400, {"error": "that file is outside the folders Dedupearr scans"}
    f = c.execute("SELECT path FROM file WHERE path=?", (path,)).fetchone()   # the lock looks at the disk too, so it only ever gets a path the scan recorded (CodeQL #13)
    if not f: return 409, {"error": "the last scan has no record of that file, so nothing was moved; scan again and look at the row once more"}
    path = f["path"]
    try: tok = lock_take(path, "moving it to the holding bin")
    except FileBusy as e: return 409, {"error": str(e)}
    try: return _hold_file(c, path, kind, why, now)
    finally: lock_drop(tok)
def _hold_file(c, path, kind, why, now=None):
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
    """Put a held file back where it was. Refuses when its old place is taken or the file in the bin is not the one that went in,
    or while Auditarr is changing the file that sits at its old place (after a relink that is the library file)."""
    r = c.execute("SELECT orig FROM held WHERE id=?", (hid,)).fetchone()
    try: tok = lock_take(r["orig"], "putting a file back from the holding bin") if r else None
    except FileBusy as e: return 409, {"error": str(e)}
    try: return _restore_held(c, hid)
    finally: lock_drop(tok)
def _restore_held(c, hid):
    r = c.execute("SELECT * FROM held WHERE id=?", (hid,)).fetchone()
    if not r: return 404, {"error": "nothing like that in the holding bin"}
    if r["restored"]: return 409, {"error": "already put back"}
    if r["purged"]: return 409, {"error": f"its {HOLD_DAYS} days ran out and it was deleted; it cannot come back"}
    try: st = os.lstat(r["held"])
    except FileNotFoundError: return 409, {"error": "the file is no longer in the holding bin (someone moved it by hand)"}
    if (st.st_ino, st.st_size) != (r["ino"], r["size"]): return 409, {"error": "the file in the holding bin is not the one that went in, so it is left alone"}
    if os.path.lexists(r["orig"]):
        so = os.lstat(r["orig"])   # a relink left a hardlink to the library file here; it may be replaced only while the library still holds the same file under another name
        if not (r["kind"] == "relink" and r["link_ino"] and stat_regular(so) and (so.st_ino, so.st_size) == (r["link_ino"], r["size"]) and so.st_nlink >= 2):
            return 409, {"error": "something new already sits at its old place, so nothing was moved; move that away first"}
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
PACK_HOLD = {"DUPLICATE_HEADER", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "DUPLICATE_COPY"}   # files of a torrent that go to the holding bin when the whole torrent is removed
PACK_STAY = {"LINKED_SEED"}   # a second name of a library file: costs nothing, stays where it is
def pack_plan(rows, ignored=()):
    """One torrent, many files (a season pack). rows = the verdicts of its files. Returns (paths to hold, how many stay, why not).
    The torrent may only go as a whole when every one of its files is either a copy the library does not need or a hardlink that costs nothing."""
    odd = [r for r in rows if r["code"] not in PACK_HOLD | PACK_STAY]
    if odd: return [], 0, f"its torrent also holds {len(odd)} file{'s' if len(odd) != 1 else ''} the library does not have ({LABEL.get(odd[0]['code'], odd[0]['code']).lower()}); decide {'those' if len(odd) != 1 else 'that one'} first"
    ign = [r for r in rows if r["path"] in ignored]
    if ign: return [], 0, f"you chose to ignore {len(ign)} file{'s' if len(ign) != 1 else ''} of its torrent; un-ignore {'them' if len(ign) != 1 else 'it'} to remove the whole torrent"
    return [r["path"] for r in rows if r["code"] in PACK_HOLD], sum(1 for r in rows if r["code"] in PACK_STAY), ""
def pack_rows(c, ci):
    """The verdicts of every scanned file that belongs to the same torrent as ci (as of the last scan)."""
    if not ci or ci.get("client") not in REMOVE_READERS: return []
    out = []
    for r in c.execute("SELECT * FROM verdict WHERE client IS NOT NULL AND client != ''"):
        try: k = json.loads(r["client"])
        except ValueError: continue
        if k.get("client") == ci["client"] and k.get("id") == ci.get("id"): out.append(dict(r))
    return out
def remove_fix(c, v, steps, now=None):
    """P3.2: a leftover or second copy whose seeding is done. The file goes to the holding bin first; then the client is told to forget the
    torrent (never its files); if the client refuses, the file goes straight back. Returns (code, obj)."""
    path = v["path"]; ci = json.loads(v["client"]) if isinstance(v.get("client"), str) and v["client"] else (v.get("client") or None)
    why = f"{v['code']}: " + " | ".join(steps); torrent = None
    if no_remove_call(ci): return 409, {"error": no_remove_sentence(ci)}
    if ci and ci.get("client") in REMOVE_READERS:
        try: fresh = REMOVE_READERS[ci["client"]]()
        except Exception as e: return 502, {"error": f"{ci['client']} did not answer just now ({e}), so nothing was changed; try again in a minute"}
        mine = {p: i for p, i in fresh.items() if i.get("id") == ci.get("id") and i.get("client") == ci["client"]}
        if mine:   # the torrent is still there: judge it on what the client says now, not on last night's scan
            cur = mine.get(path) or next(iter(mine.values()))
            if not seeding_done(cur): return 409, {"error": "the client says this torrent is not past your seeding rule any more, so nothing was changed. " + seed_sentence(cur)}
            others = [p for p in mine if p != path and os.path.exists(p) and os.path.getsize(p) >= MIN_SIZE_MB * 1024 * 1024]
            torrent = cur
            if others: return remove_pack(c, v, torrent, [path] + others, why, now)
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
# ---------------------------------------------------------------------------------------------------------------- relink (P3.3)
FIXING = {"path": None, "since": 0, "done": 0, "total": 0, "last": None}   # one long fix at a time; the page shows its progress and its result
def same_bytes(a, b, tick=None):
    """True when the two files hold exactly the same bytes. Read side by side, so it stops at the first difference and trusts no stored checksum."""
    with open(a, "rb", buffering=0) as fa, open(b, "rb", buffering=0) as fb:
        while True:
            x = fa.read(8 * 1024 * 1024); y = fb.read(8 * 1024 * 1024)
            if x != y: return False
            if not x: return True
            if tick: tick(len(x))
def relink_fix(c, v, steps, now=None, tick=None):
    """P3.3: the download is a second copy of a library file. Compare every byte, move the copy to the holding bin, put a hardlink to the library
    file at its name (the client keeps seeding the same bytes), check the two names are one file. Any failure puts the copy back. Returns (code, obj).
    Both files stay locked for the whole fix, so Auditarr never rewrites the library file while it is being compared and linked."""
    toks = []
    try:
        for p in (v["path"], v.get("twin") or ""): toks.append(lock_take(p, "making two copies one file"))
        return _relink_fix(c, v, steps, now, tick)
    except FileBusy as e: return 409, {"error": str(e)}
    finally:
        for t in toks: lock_drop(t)
def _relink_fix(c, v, steps, now=None, tick=None):
    path, twin = v["path"], v.get("twin") or ""; ci = json.loads(v["client"]) if isinstance(v.get("client"), str) and v["client"] else (v.get("client") or None)
    if ci and not ci.get("finished"): return 409, {"error": "the client is still downloading this file, so nothing was changed"}
    rows = {r["path"]: r for r in c.execute("SELECT path, dev, ino, size, kind FROM file WHERE path IN (?, ?)", (path, twin))}
    if path not in rows or twin not in rows or rows[twin]["kind"] != "library": return 409, {"error": "the last scan has no record of both files, so nothing was changed; scan again and look at the row once more"}
    path, twin = rows[path]["path"], rows[twin]["path"]
    def look():
        try: a, b = os.lstat(path), os.lstat(twin)
        except FileNotFoundError: return None, "one of the two files is no longer there; the next scan drops the row"
        if not (stat_regular(a) and stat_regular(b)): return None, "one of the two is not a plain file, so both are left alone"
        if (a.st_dev, a.st_ino, a.st_size) != tuple(rows[path][k] for k in ("dev", "ino", "size")) or (b.st_dev, b.st_ino, b.st_size) != tuple(rows[twin][k] for k in ("dev", "ino", "size")): return None, "one of the two files changed since the last scan, so nothing was changed; scan again and look at the row once more"
        if a.st_dev != b.st_dev: return None, "the two files are on different disks, and one file cannot have a name on two disks; nothing was changed"
        if a.st_ino == b.st_ino: return None, "the two names are already one file; the next scan shows it"
        return (a, b), ""
    st, err = look()
    if not st: return 409, {"error": err}
    before = [(s.st_size, s.st_mtime_ns) for s in st]
    try: same = same_bytes(path, twin, tick)
    except OSError as e: return 500, {"error": f"one of the two files could not be read ({e.strerror or e}), so nothing was changed"}
    if not same: return 409, {"error": "the two files are not identical byte for byte after all, so nothing was changed; the next scan judges them again"}
    st, err = look()
    if not st or [(s.st_size, s.st_mtime_ns) for s in st] != before: return 409, {"error": err or "one of the two files was written to during the comparison, so nothing was changed"}
    code, obj = hold_file(c, path, "relink", f"{v['code']}: " + " | ".join(steps), now)
    if code != 200: return code, obj
    def undo(msg):
        try:
            if os.path.lexists(path) and os.lstat(path).st_ino == st[1].st_ino: os.remove(path)   # only ever the link this fix just made
        except OSError: pass
        back, _ = restore_held(c, obj["held_id"])
        record_action(c, "on", "relink", path, msg, "the copy was put back; nothing was changed" if back == 200 else "the copy waits in the holding bin, Put back returns it")
        return 500, {"error": msg + (" The copy is back in its place; nothing was changed." if back == 200 else " The copy waits in the holding bin; Put back (Actions tab) returns it.")}
    try: os.link(twin, path)
    except OSError as e: return undo("The two files sit on different disks of the pool, and one file cannot have a name on two disks." if e.errno == 18 else f"The hardlink could not be made ({e.strerror or e}).")
    try: a, b = os.lstat(path), os.lstat(twin); one = a.st_ino == b.st_ino and a.st_dev == b.st_dev and b.st_nlink >= 2
    except OSError: one = False
    if not one: return undo("After linking, the two names were not one file.")
    c.execute("UPDATE held SET link_ino=?, note=? WHERE id=?", (b.st_ino, "its old name now points at the library file; Put back swaps this copy back in", obj["held_id"])); c.commit()
    record_action(c, "on", "relink", path, f"linked to the library file {twin}", "done; both names are one file, the old copy waits in the holding bin")
    obj["note"] = "Done: the download's name now points at the library file, so both are one file" + (f" and {ci['client']} keeps seeding it" if ci and not is_usenet(ci) else "") + f". The old copy waits in the holding bin; {obj['frees_h']} comes back after {HOLD_DAYS} days, and Put back (Actions tab) undoes it until then."
    return 200, obj
def relink_job(v, steps):
    """The relink runs beside the page (comparing two big files takes minutes); FIXING carries its progress and, at the end, its result."""
    c = db(); path = v["path"]
    def tick(n): FIXING["done"] += n
    try: code, obj = relink_fix(c, v, steps, tick=tick)
    except Exception as e: note_error(f"fix failed for {path}: {e!r}"); code, obj = 500, {"error": f"the fix stopped with an error and is written on the About tab: {e}"}
    try:
        if code != 200: record_action(c, "on", "relink", path, " | ".join(steps), "refused: " + obj.get("error", ""))
    finally:
        c.close(); log(f"fix relink -> {code} for {path}")
        FIXING.update(path=None, last={"path": path, "ok": code == 200, "note": obj.get("note") or obj.get("error", ""), "at": time.time()})
def remove_pack(c, v, torrent, paths, why, now=None):
    """P3.5: a torrent with several large files (a season pack) goes as a whole or not at all. Every file must be a copy the library does not need
    or a hardlink; the copies go to the holding bin, then the client forgets the torrent; any failure puts every file back."""
    rows = {r["path"]: r for r in (dict(x) for x in c.execute(f"SELECT * FROM verdict WHERE path IN ({','.join('?' * len(paths))})", paths))}
    unknown = [p for p in paths if p not in rows]
    if unknown: return 409, {"error": f"this torrent holds {len(unknown)} large file(s) the last scan has no verdict for, so nothing was changed; scan again and look at the row once more"}
    hold, stay, why_not = pack_plan(list(rows.values()), {r["path"] for r in c.execute("SELECT path FROM ignored")})
    if why_not: return 409, {"error": "Nothing was changed: " + why_not + "."}
    held = []
    def back(msg, code):
        ok = all(restore_held(c, h)[0] == 200 for h in reversed(held))
        record_action(c, "on", "remove", v["path"], why, msg + ("; every file was put back" if ok else "; some files wait in the holding bin, Put back returns them"))
        return code, {"error": msg[0].upper() + msg[1:] + ". " + ("Every file is back in its place; nothing was changed." if ok else "Some files wait in the holding bin; Put back (Actions tab) returns them.")}
    freed = 0
    for p in hold:
        code, obj = hold_file(c, p, "remove", f"{rows[p]['code']}: part of the torrent '{torrent['name']}', removed as a whole", now)
        if code != 200: return back(f"one file of the torrent could not be moved ({obj.get('error', '')})", code)
        held.append(obj["held_id"]); freed += obj["frees"]
    try: client_remove(torrent)
    except Exception as e: return back(f"{torrent['client']} would not remove the torrent ({e})", 502)
    c.execute(f"UPDATE held SET note=? WHERE id IN ({','.join('?' * len(held))})", [f"its torrent '{torrent['name']}' was removed from {torrent['client']}; Put back returns the file, not the torrent"] + held); c.commit()
    record_action(c, "on", "remove", v["path"], f"removed the torrent '{torrent['name']}' from {torrent['client']} (files kept)", f"done; {len(held)} files wait in the holding bin" + (f", {stay} linked to the library stayed" if stay else ""))
    return 200, {"ok": True, "dry_run": False, "held_ids": held, "files": len(held), "frees": freed, "frees_h": human(freed),
                 "note": f"Removed the torrent from {torrent['client']} and moved its {len(held)} files to the holding bin" + (f"; {stay} that {'are' if stay != 1 else 'is'} linked to the library stayed" if stay else "") + f". {human(freed)} comes back after {HOLD_DAYS} days; Put back (Actions tab) returns each file until then, not the torrent."}
LIVE_KINDS = {"bin", "remove", "relink", "empty"}   # P3.4 (2026-11-14): old recycle-bin files go to the holding bin like everything else. P3.3 (2026-11-14): relink an identical copy. P3.1: orphans to the holding bin. P3.2 (2026-11-14): remove a leftover or second copy through the client. Relink and emptying the recycle bin stay dry runs until their gates pass
def ui_fix(c, path, background=False):
    """POST /api/fix: show the plan (dry run) or carry it out (on). Phase 3 ships the 'on' branch fix by fix; until then every call is a dry run."""
    if SCANNING["on"]: return 409, {"error": "a scan is running; try again in a minute, so a fix never races the scan"}
    if FIXING["path"]: return 409, {"error": "another fix is still comparing two big files; try again when it is done"}
    r = c.execute("SELECT * FROM verdict WHERE path=?", (path,)).fetchone()
    if not r: return 404, {"error": "that file is not in the last scan"}
    v = dict(r); v["removable"] = bool(v.get("removable"))
    kind, steps, frees = plan_fix(v)
    if not kind: return 400, {"error": steps[0]}
    if kind == "remove":   # a torrent with several files goes as a whole: say so in the plan, and say why not when it cannot
        ci = json.loads(v["client"]) if v.get("client") else None; pack = pack_rows(c, ci)
        if len(pack) > 1:
            hold, stay, why_not = pack_plan(pack, {r["path"] for r in c.execute("SELECT path FROM ignored")})
            if why_not: return 409, {"error": "Nothing was changed: " + why_not + "."}
            frees = sum(r["size"] for r in pack if r["path"] in hold)
            steps = [steps[0], f"Move all {len(hold)} files of this torrent that the library does not need into the holding bin under {HOLDING_DIR} ({human(frees)}; nothing is deleted)."] + ([f"Leave the {stay} file{'s' if stay != 1 else ''} that {'are' if stay != 1 else 'is'} linked to the library where {'they are' if stay != 1 else 'it is'}; {'they cost' if stay != 1 else 'it costs'} no space."] if stay else []) + [steps[-1].replace("Keep it", "Keep them").replace("delete it", "delete them")]
    if FIX_MODE == "off": return 403, {"error": "fixes are switched off (FIX_MODE=off); the plan is still shown on the row"}
    if FIX_MODE == "on" and kind == "relink" and background:
        FIXING.update(path=path, since=time.time(), done=0, total=v.get("size") or 0); threading.Thread(target=relink_job, args=(v, steps), daemon=True).start()
        return 202, {"ok": True, "dry_run": False, "started": True, "kind": kind, "steps": steps, "note": "Comparing both files byte for byte first; a big file takes a few minutes. The row shows the progress and the result."}
    if FIX_MODE == "on" and kind in LIVE_KINDS:
        code, obj = relink_fix(c, v, steps) if kind == "relink" else remove_fix(c, v, steps) if kind == "remove" else hold_file(c, path, kind, f"{v['code']}: " + " | ".join(steps))
        if code != 200: record_action(c, "on", kind, path, " | ".join(steps), "refused: " + obj.get("error", ""))
        return code, dict(obj, kind=kind, steps=steps)
    later = FIX_MODE == "on" and kind not in LIVE_KINDS
    record_action(c, "dryrun", kind, path, " | ".join(steps), f"dry run: would free {human(frees)}")
    return 200, {"ok": True, "dry_run": True, "kind": kind, "steps": steps, "frees": frees, "frees_h": human(frees),
                 "note": f"Dry run: nothing was changed. This fix would free {human(frees)} in {len(steps)} steps." + (" This kind of fix is not switched on yet; it arrives after its own safety checks." if later else "")}

BULK_MAX = 500
def in_bulk(kind, ci):
    """May this fix run from 'Move all shown'? Only a plain move that Put back undoes in full: removing a torrent cannot be undone, so that stays one press per row."""
    return FIX_MODE == "on" and kind in LIVE_KINDS and kind != "relink" and not (kind == "remove" and ci and ci.get("client") in REMOVE_READERS)
def ui_fix_all(c, paths):
    """POST /api/fix_all: move every listed file to the holding bin, one after the other, each through the same checks as its own button.
    A file that is refused is left alone and named; the rest still go. Returns (code, obj)."""
    if FIX_MODE != "on": return 403, {"error": "moving many files at once only works when fixes are switched on"}
    if SCANNING["on"]: return 409, {"error": "a scan is running; try again in a minute, so a fix never races the scan"}
    if len(paths) > BULK_MAX: return 400, {"error": f"that is more than {BULK_MAX} files at once; narrow the list with a filter or the search box"}
    ignored = {r["path"] for r in c.execute("SELECT path FROM ignored")}; done = freed = 0; skipped = []
    for path in dict.fromkeys(paths):
        r = c.execute("SELECT * FROM verdict WHERE path=?", (path,)).fetchone()
        if not r: skipped.append({"path": path, "error": "that file is not in the last scan"}); continue
        ci = json.loads(r["client"]) if r["client"] else None
        if path in ignored: skipped.append({"path": path, "error": "you chose to ignore this file"}); continue
        if not in_bulk(FIXABLE.get(r["code"]), ci): skipped.append({"path": path, "error": "this one is not a plain move to the holding bin; use the button on its own row"}); continue
        try: code, obj = ui_fix(c, path)
        except Exception as e: note_error(f"fix failed for {path}: {e!r}"); code, obj = 500, {"error": f"stopped with an error: {e}"}
        if code == 200 and not obj.get("dry_run"): done += 1; freed += obj.get("frees", 0)
        else: skipped.append({"path": path, "error": obj.get("error", "nothing was changed")})
    for k in skipped: k["title"] = friendly_title(k["path"])
    note = (f"Moved {done} file{'s' if done != 1 else ''} to the holding bin. {human(freed)} comes back after {HOLD_DAYS} days; Put back (Actions tab) undoes each one until then." if done else "Nothing was moved.")
    if skipped: note += f" {len(skipped)} {'was' if len(skipped) == 1 else 'were'} left alone: {skipped[0]['error']}" + (" (and other reasons; see the Actions tab)" if len({k['error'] for k in skipped}) > 1 else "")
    return 200, {"ok": True, "dry_run": False, "done": done, "frees": freed, "frees_h": human(freed), "skipped": skipped, "note": note}

RECLAIM = ("DUPLICATE_COPY", "DUPLICATE_HEADER", "BIN_OLD", "LEFTOVER", "ORPHAN")   # the kinds counted as "wasted, can come back"
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
        v["auditarr"] = json.loads(v["auditarr"]) if v.get("auditarr") else None
        ci = json.loads(v["client"]) if v.get("client") else None; v["client"] = ci; v["client_text"] = client_text(ci) if (ci or (v["kind"] == "download" and known)) else ""
        v["removable"] = bool(v.get("removable")) and v["needs_you"]
        v["category"] = v.get("category") if v.get("category") in CAT_LABEL else "other"; v["category_label"] = CAT_LABEL[v["category"]]
        v["fixable"] = v["code"] in FIXABLE and v["needs_you"] and (v["code"] in ("ORPHAN", "BIN_OLD") or v["removable"] or (v["code"] == "DUPLICATE_COPY" and (not ci or bool(ci.get("finished")))))
        v["fix_kind"] = FIXABLE.get(v["code"]) if v["fixable"] else ""
        v["bulk"] = bool(v["fixable"] and in_bulk(v["fix_kind"], ci))   # may go with 'Move all shown'
        v["wait"] = seed_wait(v.get("client") if isinstance(v.get("client"), dict) else None) if v["needs_you"] and not v["fixable"] and v["code"] in FIXABLE else ""   # why there is no button yet
        if v["fix_kind"] == "remove" and no_remove_call(ci): v["fixable"] = False; v["fix_kind"] = ""; v["bulk"] = False; v["wait"] = f"Remove it in {ci['client']} first (keep its files)"
        items.append(v)
    packs = {}
    for v in items:
        k = v["client"]
        if k and k.get("client") in REMOVE_READERS: packs.setdefault((k["client"], k.get("id")), []).append(v)
    for group in packs.values():   # P3.5: a torrent with several files goes as a whole or not at all, and its rows say which
        if len(group) < 2: continue
        hold, stay, why_not = pack_plan(group, ignored); size = sum(v["size"] for v in group if v["path"] in hold)
        for v in group:
            if v["fix_kind"] != "remove": continue
            if why_not: v["fixable"] = False; v["fix_kind"] = ""; v["bulk"] = False; v["wait"] = why_not[0].upper() + why_not[1:]
            else: v["pack"] = {"files": len(hold), "stay": stay, "size_h": human(size)}
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
    reclaim = sum(bytes_by.get(k, 0) for k in RECLAIM); decide = bytes_by.get("DOWNLOAD_UNMATCHED", 0) + bytes_by.get("DOWNLOAD_OTHER_RELEASE", 0)
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
            "clients": client_status(), "auditarr": dict(auditarr_status()), "seed_rule": {"ratio": SEED_MIN_RATIO, "days": SEED_MIN_DAYS, "rule": SEED_RULE}, "fix_mode": FIX_MODE, "live_kinds": sorted(LIVE_KINDS), "fixing": dict(FIXING), "holding_dir": HOLDING_DIR, "holding": holding(c), "nightly_bot": nightly_bot()}

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
    au = auditarr_status()
    if au: health.append({"level": "ok", "text": f"Auditarr: {au['note']}; its trash is left alone."} if au["ok"] else {"level": "warn", "text": f"Auditarr could not be read: {au['note']}. Files it changed are explained less precisely until it answers."})
    for n in discovery()["notes"]: health.append({"level": "ok", "text": n})
    if not any(c["url"] for c in CLIENTS.values()) and not any(a["url"] for a in ARRS.values()): health.append({"level": "warn", "text": "No download client or arr is connected, so downloads without a match stay undecided. Connect Sonarr or Radarr (SONARR_URL / RADARR_URL) and Dedupearr finds their download clients itself; see the README."})
    elif not client_status() and last: health.append({"level": "ok", "text": "Clients and arrs are configured; they are asked on the next scan."})
    health.append({"level": "ok", "text": {"off": "Fixes are switched off.", "dryrun": "Fixes run as a dry run: every 'Preview fix' shows the exact steps and nothing is moved, linked or deleted.", "on": "Fixes are on: files move to the holding bin first and can be undone for %d days." % HOLD_DAYS}.get(FIX_MODE, FIX_MODE)})
    if NTFY_URL and DIGEST == "on" and not SANDBOX: health.append({"level": "ok", "text": "A morning message goes to your phone after each nightly scan."})
    if SANDBOX: health.append({"level": "ok", "text": SANDBOX_NOTE})
    return {"app": APP, "tagline": TAGLINE, "version": VERSION, "revision": REVISION, "phase": PHASE, "health": health,
            "settings": {"SCAN_ROOTS": SCAN_ROOTS, "LIBRARY_DIRS": LIBRARY_DIRS, "DOWNLOAD_DIRS": DOWNLOAD_DIRS, "RECYCLE_DIRS": RECYCLE_DIRS,
                         "RECYCLE_DAYS": RECYCLE_DAYS, "MIN_SIZE_MB": MIN_SIZE_MB, "HASH_BUDGET_MB": HASH_BUDGET_MB, "SCAN_TIME": SCAN_TIME,
                         "SEED_MIN_RATIO": SEED_MIN_RATIO, "SEED_MIN_DAYS": SEED_MIN_DAYS, "SEED_RULE": SEED_RULE, "CLIENT_PATH_MAP": CLIENT_PATH_MAP, "FIX_MODE": FIX_MODE, "HOLDING_DIR": HOLDING_DIR, "HOLD_DAYS": HOLD_DAYS,
                         "connections": {**{a: (c["url"] or "not set") for a, c in ARRS.items()}, **{n: (c["url"] or "not set") for n, c in CLIENTS.items()}, "Auditarr": AUDITARR_URL or "not set"}},
            "runs": [dict(r) for r in c.execute("SELECT * FROM run ORDER BY id DESC LIMIT 30")], "log": LOG[-60:], "errors": errors_recent()[-30:],
            "links": {"source": "https://github.com/chrizantoz/dedupearr", "auditarr": "https://github.com/chrizantoz/auditarr"}}

# ------------------------------------------------------------------------------------------------------------------- the morning message (P2.6)
# One message a day, after the nightly scan: what needs you, how much space can come back, what was moved since yesterday, what waits in the holding bin.
# It goes to an ntfy topic (NTFY_URL) and is also served at /api/digest, where Auditarr reads it to put both apps in one message.
NTFY_URL = _env("NTFY_URL"); NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "").strip(); NTFY_TOKEN_FILE = _env("NTFY_TOKEN_FILE"); PUBLIC_URL = _env("PUBLIC_URL").rstrip("/")
DIGEST = (_env("DIGEST", "on") or "on").lower()
def _files(n): return f"{n} file{'s' if n != 1 else ''}"
def digest(c, now=None, hours=26):
    """The morning message as data: title, lines, text. Reads only; never changes anything."""
    now = now or time.time(); since = now - hours * 3600; s = state(c); last = s["last_run"]
    needs = [v for v in s["items"] if v["needs_you"]]; back = s["reclaim_bytes"]; lines = []; trouble = []
    can_go = sum(v["wasted"] for v in needs if v["fixable"] and v["code"] in RECLAIM)   # the part of "to get back" that has a fix button today
    if not last: lines.append("No scan has run yet.")
    elif not needs: lines.append("Needs you: nothing")
    else:
        lines.append(f"Needs you: {_files(len(needs))}, {human(back)} to get back")
        kinds = {}
        for v in needs: k = kinds.setdefault(v["label"], [0, 0]); k[0] += 1; k[1] += v["wasted"]
        top = sorted(kinds.items(), key=lambda kv: (-kv[1][1], -kv[1][0], kv[0]))
        lines += [f"  - {lab}: {_files(n)}" + (f", {human(b)}" if b else "") for lab, (n, b) in top[:4]]
        if len(top) > 4: lines.append(f"  ... and {len(top) - 4} more kinds")
        if can_go: lines.append(f"Can go right now: {human(can_go)}")
        if s["decide_bytes"]: lines.append(f"Still undecided: {s['decide_h']} of downloads")
    moved = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM held WHERE at >= ?", (since,)).fetchone()
    put_back = c.execute("SELECT COUNT(*) AS n FROM held WHERE restored >= ?", (since,)).fetchone()["n"]
    gone = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM held WHERE purged >= ?", (since,)).fetchone()
    did = ([f"{_files(moved['n'])} moved to the holding bin ({human(moved['b'])})"] if moved["n"] else []) + ([f"{put_back} put back"] if put_back else []) \
        + ([f"{gone['n']} deleted for good ({human(gone['b'])} back)"] if gone["n"] else [])
    lines.append("Since yesterday: " + (", ".join(did) if did else "nothing was moved"))
    h = holding(c, now)
    if h["items"]:
        soon = sum(1 for i in h["items"] if i["days_left"] <= 1)
        lines.append(f"Holding bin: {_files(len(h['items']))}, {h['bytes_h']}" + (f"; {soon} will be deleted for good within a day" if soon else ""))
    if last and last["note"] != "ok": trouble.append("the last scan failed")
    elif last and now - last["finished"] > 2 * 86400: trouble.append("the last scan is more than two days old")
    trouble += [f"{name} could not be read" for name, st_ in sorted(s["clients"].items()) if not st_["ok"]] + (["Auditarr could not be read"] if s["auditarr"] and not s["auditarr"]["ok"] else [])
    errs = errors_recent()
    if errs: trouble.append(f"{len(errs)} error{'s' if len(errs) != 1 else ''} in the last 24 hours")
    lines.append("Trouble: " + "; ".join(trouble) if trouble else "No errors since yesterday")
    title = f"{APP} daily: " + (f"{len(needs)} need you · {human(back)} to get back" if needs else "nothing needs you")
    return {"app": APP, "title": title, "lines": lines, "text": "\n".join(lines), "priority": "high" if trouble else "default", "needs": len(needs), "reclaim_bytes": back, "reclaim_h": human(back),
            "now_bytes": can_go, "decide_bytes": s["decide_bytes"], "trouble": trouble, "click": PUBLIC_URL + "/" if PUBLIC_URL else "", "phone": bool(NTFY_URL) and DIGEST == "on" and not SANDBOX}
def ntfy(title, body, priority="default", click=""):
    """Push one message to the ntfy topic. -> True when it was delivered. The public demo never sends."""
    if not NTFY_URL or SANDBOX: return False
    headers = {"Title": title.encode("utf-8").decode("latin-1"), "Priority": priority, "Tags": "package"}
    if click: headers["Click"] = click
    tok = NTFY_TOKEN
    if not tok and NTFY_TOKEN_FILE and os.path.exists(NTFY_TOKEN_FILE): tok = open(NTFY_TOKEN_FILE).read().strip()
    if tok: headers["Authorization"] = "Bearer " + tok
    try: urllib.request.urlopen(urllib.request.Request(NTFY_URL, body.encode(), headers), timeout=20).read(); return True
    except Exception as e: note_error(f"the morning message could not be sent: {type(e).__name__}"); return False
def send_digest(c=None):
    """After the nightly scan: send the morning message when a topic is set and DIGEST is on."""
    if not NTFY_URL or DIGEST != "on" or SANDBOX: return False
    own = c is None; c = c or db()
    try: d = digest(c); return ntfy(d["title"], d["text"], d["priority"], d["click"])
    finally:
        if own: c.close()

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

# ------------------------------------------------------------------------------------------------------------------- settings (P1.7) and setup (P4.1)
# Saved on the Settings and Setup tabs into the database in the config folder (kv 'settings'). A value set on the container always wins and
# shows as locked on the page ("set on the container; change it there"). FIX_MODE, HOLD_DAYS and the folders to scan stay container-only.
_HINT_DEFAULTS = {"LIBRARY_HINTS": "media,library,movies,movie,films,tv,shows,series,anime,plex,jellyfin",
                  "DOWNLOAD_HINTS": "torrents,torrent,downloads,download,complete,completed,incomplete,intermediate,seeding,seeds,usenet,nzb,nzbget,sabnzbd,qbittorrent,deluge",
                  "RECYCLE_HINTS": ".recycle,.trash,recycle,trash,.recyclebin,#recycle,@recycle,.trash-1000"}
SETTABLE = {   # name -> (group, kind, default, label, hint)
    "RECYCLE_DAYS": ("rules", "int", "30", "Days a file may sit in a recycle bin", "Older than this, a recycle-bin file counts as waste."),
    "SEED_MIN_RATIO": ("rules", "num", "1.0", "Seeding ratio to reach", "A torrent is done seeding once it reaches this ratio (and the days below, as the rule says)."),
    "SEED_MIN_DAYS": ("rules", "num", "14", "Days of seeding to reach", "A torrent is done seeding after this many days (and the ratio above, as the rule says)."),
    "SEED_RULE": ("rules", "choice:auto,both,either", "auto", "Which of the two", "auto: private torrents need both, public ones either. both: always both. either: one is enough."),
    "MIN_SIZE_MB": ("rules", "int", "50", "Smallest file to judge (MB)", "Smaller video files, such as samples, are left out of every scan."),
    "SCAN_TIME": ("rules", "time", "04:30", "Nightly scan time", "Hours and minutes, in the container's time zone."),
    "LIBRARY_DIRS": ("rules", "dirs", "", "Library folders", "Only when their names do not say so: folders inside the scanned folders, separated by commas."),
    "DOWNLOAD_DIRS": ("rules", "dirs", "", "Download folders", "Only when their names do not say so: folders inside the scanned folders, separated by commas."),
    "RECYCLE_DIRS": ("rules", "dirs", "", "Recycle-bin folders", "Only when their names do not say so: folders inside the scanned folders, separated by commas."),
    "LIBRARY_HINTS": ("rules", "words", _HINT_DEFAULTS["LIBRARY_HINTS"], "Folder names that mean library", "A folder with one of these names, anywhere in the path, is library. Separated by commas."),
    "DOWNLOAD_HINTS": ("rules", "words", _HINT_DEFAULTS["DOWNLOAD_HINTS"], "Folder names that mean downloads", "Separated by commas."),
    "RECYCLE_HINTS": ("rules", "words", _HINT_DEFAULTS["RECYCLE_HINTS"], "Folder names that mean a recycle bin", "These win over the two lists above. Separated by commas."),
}
CONN_GROUPS = {"Sonarr": ["SONARR_URL", "SONARR_APIKEY"], "Radarr": ["RADARR_URL", "RADARR_APIKEY"], "Whisparr": ["WHISPARR_URL", "WHISPARR_APIKEY"], "Sportarr": ["SPORTARR_URL", "SPORTARR_APIKEY"],
               "Lidarr": ["LIDARR_URL", "LIDARR_APIKEY"], "Deluge": ["DELUGE_URL", "DELUGE_PASSWORD"], "NZBGet": ["NZBGET_URL", "NZBGET_USER", "NZBGET_PASSWORD"], "qBittorrent": ["QBIT_URL", "QBIT_USER", "QBIT_PASSWORD"],
               "rTorrent": ["RTORRENT_URL", "RTORRENT_USER", "RTORRENT_PASSWORD"], "Transmission": ["TRANSMISSION_URL", "TRANSMISSION_USER", "TRANSMISSION_PASSWORD"], "SABnzbd": ["SABNZBD_URL", "SABNZBD_APIKEY"], "Auditarr": ["AUDITARR_URL"]}
for _g, _names in CONN_GROUPS.items():
    for _n in _names: SETTABLE[_n] = ("conn:" + _g, "url" if _n.endswith("_URL") else "user" if _n.endswith("_USER") else "secret", "", {"url": "Address", "user": "User"}.get(_n.rsplit("_", 1)[1].lower(), "API key" if _n.endswith("APIKEY") else "Password"), "")
SETTINGS = {}   # what the page saved
def _envset(name): return bool(os.environ.get(name, "").strip())
def group_locked(name):
    """A connection's address and its password go together: when any of them is set on the container, the page changes none of them,
    so a password from the container is never sent to an address typed on the page."""
    g = SETTABLE[name][0]
    return any(_envset(n) for n in CONN_GROUPS[g[5:]]) if g.startswith("conn:") else _envset(name)
def setting(name):
    """(effective raw value, source): container | page | default."""
    if _envset(name) or (SETTABLE[name][0].startswith("conn:") and group_locked(name)): return os.environ.get(name, "").strip(), "container"
    if name in SETTINGS: return str(SETTINGS[name]), "page"
    return SETTABLE[name][2], "default"
def validate_setting(name, value):
    """The cleaned value, or ValueError with a sentence a person understands."""
    _, kind, default, label, _ = SETTABLE[name]; v = str(value if value is not None else "").strip(); low = label[0].lower() + label[1:]
    if len(v) > 2000 or "\n" in v or "\r" in v: raise ValueError(f"{label}: that is too long or has a line break")
    if kind == "int":
        if not re.fullmatch(r"\d{1,6}", v) or not (1 <= int(v) <= (3650 if name == "RECYCLE_DAYS" else 100000)): raise ValueError(f"{label}: a whole number, 1 or more")
        return str(int(v))
    if kind == "num":
        try: f = float(v)
        except ValueError: raise ValueError(f"{label}: a number, such as 1 or 1.5") from None
        if not (0 <= f <= (1000 if "RATIO" in name else 3650)) or f != f: raise ValueError(f"{label}: a number between 0 and {1000 if 'RATIO' in name else 3650}")
        return f"{f:g}"
    if kind.startswith("choice:"):
        if v.lower() not in kind[7:].split(","): raise ValueError(f"{label}: one of {kind[7:].replace(',', ', ')}")
        return v.lower()
    if kind == "time":
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", v)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59: raise ValueError(f"{label}: hours and minutes, such as 04:30")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if kind == "dirs":
        out = []
        for d_ in [x.strip().rstrip("/") for x in v.split(",") if x.strip()]:
            if not d_.startswith("/") or "/.." in d_ + "/" or not any(d_ == r or d_.startswith(r.rstrip("/") + "/") for r in SCAN_ROOTS): raise ValueError(f"{label}: {d_} is not a folder inside the scanned folders ({', '.join(SCAN_ROOTS)})")
            out.append(d_)
        return ",".join(dict.fromkeys(out))
    if kind == "words":
        ws = [x.strip().lower() for x in v.split(",") if x.strip()]
        if not ws: raise ValueError(f"{label}: keep at least one name; 'Use the default' brings the usual list back")
        if any("/" in w or len(w) > 60 for w in ws): raise ValueError(f"{label}: folder names only, without slashes")
        return ",".join(dict.fromkeys(ws))
    if kind == "url":
        if v and not re.fullmatch(r"https?://[A-Za-z0-9._\[\]:-]+(/[^\s]*)?", v): raise ValueError(f"{low[0].upper() + low[1:]} of {SETTABLE[name][0][5:]}: it must start with http:// or https:// and name a host, such as http://192.168.1.10:8989")
        return v.rstrip("/")
    return v   # user, secret
def refresh_globals():
    """Put the effective settings into the module's settings, the way the environment did at start."""
    g = globals(); val = lambda n: setting(n)[0]
    g["RECYCLE_DAYS"] = int(val("RECYCLE_DAYS") or 30); g["MIN_SIZE_MB"] = int(val("MIN_SIZE_MB") or 50); g["SCAN_TIME"] = val("SCAN_TIME") or "04:30"
    g["SEED_MIN_RATIO"] = float(val("SEED_MIN_RATIO") or 1.0); g["SEED_MIN_DAYS"] = float(val("SEED_MIN_DAYS") or 14); g["SEED_RULE"] = (val("SEED_RULE") or "auto").lower()
    for n in ("LIBRARY_DIRS", "DOWNLOAD_DIRS", "RECYCLE_DIRS"): g[n] = [x.strip().rstrip("/") for x in val(n).split(",") if x.strip()]
    for n in _HINT_DEFAULTS: g[n] = [x.strip().lower().rstrip("/") for x in (val(n) or _HINT_DEFAULTS[n]).split(",") if x.strip()]
    for app in ARRS: ARRS[app].update(url=val(f"{app.upper()}_URL").rstrip("/"), key=val(f"{app.upper()}_APIKEY"))
    for kind, names in CONN_GROUPS.items():
        if kind not in CLIENTS: continue
        for n in names: CLIENTS[kind]["url" if n.endswith("_URL") else "user" if n.endswith("_USER") else "key" if n.endswith("APIKEY") else "password"] = val(n).rstrip("/") if n.endswith("_URL") else val(n)
        src = setting(names[0])[1]
        if CLIENTS[kind]["url"]: CLIENT_SOURCE[kind] = "container" if src == "container" else "page"
        else: CLIENT_SOURCE.pop(kind, None)
    g["AUDITARR_URL"] = val("AUDITARR_URL").rstrip("/")
def load_settings(c=None):
    own = c is None; c = c or db()
    try: raw = kv_get(c, "settings"); SETTINGS.clear(); SETTINGS.update({k: v for k, v in (json.loads(raw) if raw else {}).items() if k in SETTABLE})
    except (ValueError, AttributeError) as e: log(f"settings: the saved settings could not be read ({e}); the container's and the defaults apply")
    finally:
        if own: c.close()
    refresh_globals()
def settings_view():
    """GET /api/settings: every setting with its value, where it comes from and whether the page may change it. Passwords and keys are never sent back."""
    items = []
    for name, (group, kind, default, label, hint) in SETTABLE.items():
        v, src = setting(name)
        items.append({"name": name, "group": group, "kind": kind, "label": label, "hint": hint, "default": default, "source": src, "locked": group_locked(name),
                      "value": "" if kind == "secret" else v, "saved": bool(v) if kind == "secret" else None})
    return {"items": items, "groups": list(CONN_GROUPS), "fixed": {"FIX_MODE": FIX_MODE, "SCAN_ROOTS": SCAN_ROOTS, "HOLD_DAYS": HOLD_DAYS}}
def save_settings(c, body, who):
    """POST /api/settings {"set": {name: value}, "reset": [names]} -> (code, obj). Values set on the container are refused by name."""
    changes = body.get("set") if isinstance(body.get("set"), dict) else {}; reset = body.get("reset") if isinstance(body.get("reset"), list) else []
    unknown = [k for k in list(changes) + reset if k not in SETTABLE]
    if unknown: return 400, {"error": f"not a setting this page can change: {', '.join(map(str, unknown))[:200]}"}
    locked = [k for k in list(changes) + reset if group_locked(k)]
    if locked: return 409, {"error": f"{SETTABLE[locked[0]][3]}{' of ' + SETTABLE[locked[0]][0][5:] if SETTABLE[locked[0]][0].startswith('conn:') else ''} is set on the container, so it is changed there, not here"}
    new = dict(SETTINGS)
    try:
        for k, v in changes.items():
            v = validate_setting(k, v)
            if SETTABLE[k][1] == "secret" and v == "" and not body.get("clear_" + k): continue   # an empty password box means 'keep the saved one'
            if v == SETTABLE[k][2]: new.pop(k, None)
            else: new[k] = v
    except ValueError as e: return 400, {"error": str(e)}
    for k in reset: new.pop(k, None)
    for g, names in CONN_GROUPS.items():   # a new address forgets the old password, so a saved password never goes to an address it was not typed for
        u = names[0]
        if new.get(u, "") != SETTINGS.get(u, "") and SETTINGS.get(u):
            for n in names[1:]:
                if SETTABLE[n][1] == "secret" and n not in changes: new.pop(n, None)
    old = dict(SETTINGS); kv_set(c, "settings", json.dumps(new)); load_settings(c)
    changed = sorted(k for k in set(new) | set(old) if new.get(k) != old.get(k))   # names only: a password never reaches the log
    record_action(c, "on", "settings", "settings", f"by {who or 'the page'}", "changed: " + ", ".join(changed)[:400] if changed else "nothing changed")
    return 200, {"ok": True, "note": ("Saved. The next scan uses it" + ("; press Scan now to see the change." if any(SETTABLE[k][0] == "rules" for k in changed) else ".")) if changed else "Nothing changed.", "settings": settings_view()}
def settings_writable(ip, headers):
    """May this request change settings? -> (yes, why not). With sign-in on, a signed-in person may. Without it, only a browser on your own
    network talking to the port directly, like the first password: through a domain nobody may point your apps somewhere else."""
    if SANDBOX: return False, "This is the public demo: its settings are fixed."
    if auth_on(): return True, ""
    if direct_local(ip, headers): return True, ""
    return False, "Settings can only be changed from your own network while the page has no sign-in. Turn on Protect the page (About tab) to change them from anywhere."

def test_connection(name):
    """POST /api/setup/test: ask one arr or client with what is saved now (never with what a request sends). Reads only. -> (ok, sentence)"""
    try:
        if name.lower() in ARRS:
            cfg = ARRS[name.lower()]
            if not cfg["url"] or not cfg["key"]: return False, f"{name} has no address or API key yet. Type them above and press Save first."
            j = http_json(f"{cfg['url']}/api/{api_v(name.lower())}/system/status", headers={"X-Api-Key": cfg["key"]}, timeout=15)
            if not isinstance(j, dict) or not j.get("version"): return False, f"Something answered at {name}'s address, but not {name} (a sign-in page or another app)."
            return True, f"{name} answered (version {str(j['version'])[:20]})."
        if name == "Auditarr":
            if not AUDITARR_URL: return False, "Auditarr has no address yet. Type it above and press Save first."
            j = http_json(AUDITARR_URL + "/api/changed", timeout=15)
            if not isinstance(j, dict) or not isinstance(j.get("files"), list): return False, "Something answered at Auditarr's address, but not Auditarr's list of changed files (an older Auditarr, or a sign-in page)."
            return True, f"Auditarr answered: it lists {len(j['files'])} files it changed."
        if name in READERS:
            cfg = CLIENTS[name] if CLIENTS[name]["url"] else ACTIVE.get(name)
            if not cfg or not cfg.get("url"): return False, f"{name} has no address yet, and none of your arrs lists it. Type it above and press Save first."
            before = dict(CLIENT_STATUS.get(name) or {}); had = name in CLIENT_STATUS
            try: got = READERS[name](cfg, 15); n = CLIENT_STATUS.get(name, {}).get("n", 0)
            finally:   # a test is not a scan: About keeps what the last scan learned
                if had: CLIENT_STATUS[name] = before
                else: CLIENT_STATUS.pop(name, None)
            return True, f"{name} answered: it knows {n} {'jobs' if name in USENET else 'torrents'} ({len(got)} files and folders)."
        return False, "Dedupearr does not know that app."
    except urllib.error.HTTPError as e: return False, f"{name} answered with an error ({e.code}{': the key or password was refused' if e.code in (401, 403) else ''})."
    except Exception as e: return False, f"{name} did not answer: {str(e)[:160]}."

def _mounts():
    """The mounts this container sees (/proc/self/mountinfo): mount point, the folder of its source it shows, filesystem."""
    out = []
    try:
        with open("/proc/self/mountinfo") as fh:
            for line in fh:
                a, _, b = line.partition(" - "); pa, pb = a.split(), b.split()
                if len(pa) >= 5 and pb: out.append({"point": pa[4].replace("\\040", " "), "root": pa[3].replace("\\040", " "), "fs": pb[0]})
    except OSError: pass
    return out
def _mount_of(path, mounts):
    best = None
    for m in mounts:
        p = m["point"].rstrip("/") or "/"
        if (p == "/" or path == p or path.startswith(p + "/")) and (best is None or len(p) > len(best["point"].rstrip("/") or "/")): best = m
    return best
def mount_test(c, mounts=None):
    """P4.1, the shared-mount test: can a library file and a download be one file? Two answers, from what is known without guessing:
    Dedupearr's own view (are the library and the downloads on one mount here) and each arr's import history (does it move files between
    two different top folders, which inside its container usually means two mounts and therefore a copy for every import).
    -> {"ok": True | False | None, "lead": sentence, "lines": [{"level", "text"}]}"""
    mounts = _mounts() if mounts is None else mounts; lines = []
    rows = c.execute("SELECT path, dev, kind FROM file WHERE kind IN ('library','download')").fetchall()
    lib = [r for r in rows if r["kind"] == "library"]; dl = [r for r in rows if r["kind"] == "download"]
    links = c.execute("SELECT COUNT(*) FROM verdict WHERE code='REAL_COPY'").fetchone()[0]; twice = c.execute("SELECT COUNT(*) FROM verdict WHERE code IN ('DUPLICATE_COPY','DUPLICATE_HEADER')").fetchone()[0]
    ok = None
    if not lib or not dl:
        lead = "Scan first: the test needs at least one library file and one download." if not rows else f"The last scan found {'no library files' if not lib else 'no downloads'}, so there is nothing to compare. Check the folder names on the Settings tab."
    else:
        mp = lambda rs: sorted({(_mount_of(r["path"], mounts) or {"point": "/"})["point"] for r in rs}); lm, dm = mp(lib), mp(dl)
        same_dev = bool({r["dev"] for r in lib} & {r["dev"] for r in dl})
        if set(lm) & set(dm) and same_dev:
            ok = True; lead = "Your library and your downloads sit on one mount here, so a download and its library file can be one file."
            lines.append({"level": "ok", "text": f"The last scan found {links} library file{'s that share their' if links != 1 else ' that shares its'} bytes with a download: hardlinks work." if links else "No library file shares its bytes with a download yet." + (" Your arrs copy instead of linking; see below." if twice else "")})
        else:
            ok = False; cp = os.path.commonpath(lm + dm) if lm and dm else "/"
            lead = (f"Your library ({', '.join(lm)}) and your downloads ({', '.join(dm)}) are two separate mounts here, so they can never be one file, and Make them one file cannot work."
                    if not set(lm) & set(dm) else "Your library and your downloads are on two different disks or shares, so a hardlink cannot join them.")
            lines.append({"level": "warn", "text": f"The one-line change: mount the folder on your server that holds both once, for example -v /mnt/user/data:{cp if cp != '/' else '/data'}, instead of one -v line for each, and point Dedupearr's scanned folders at the folders inside it."})
    lay = LAYOUT
    if not lay:
        try: lay = json.loads(kv_get(c, "arr_layout") or "{}")
        except ValueError: lay = {}
    for app, L in sorted(lay.items()):
        if not isinstance(L, dict) or not L.get("from") or not L.get("to"): continue
        A = app.capitalize()
        if L["from"] == L["to"]: lines.append({"level": "ok", "text": f"{A} takes downloads and puts the library under one folder ({L['from']}), so it can link instead of copying."})
        else:
            lines.append({"level": "warn", "text": f"{A} takes its downloads from {L['from']} and puts the library in {L['to']}. If those are two mounts in {A}'s own container, {A} copies every import instead of linking it. The one-line change there: give {A} one mount for both, for example /data, with the downloads at /data/torrents and the library at /data/media."})
            ok = False   # something to change, even when Dedupearr's own view is fine
    return {"ok": ok, "lead": lead, "lines": lines}
def setup_view(c):
    """GET /api/setup: the shared-mount test, what the arrs list, and what each connection said on the last scan."""
    st = client_status(); conns = []
    for g, names in CONN_GROUPS.items():
        u, src = setting(names[0])
        if g in CLIENTS and not u and ACTIVE.get(g, {}).get("url"): u, src = ACTIVE[g]["url"], "arr"
        known = st.get(g) or st.get(g.capitalize()) or {}
        conns.append({"name": g, "kind": "arr" if g.lower() in ARRS else "auditarr" if g == "Auditarr" else "usenet" if g in USENET else "torrent", "fields": names, "url": u, "source": src, "locked": group_locked(names[0]),
                      "status": ("ok" if known.get("ok") else "warn") if known else "", "note": str(known.get("note") or "")[:200]})
    return {"mount": mount_test(c), "discovered": discovery(), "connections": conns}

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
                if p == "/api/digest": return self._json(200, digest(c))
                if p == "/api/settings": ok_, why = settings_writable(self.client_address[0], self.headers); return self._json(200, dict(settings_view(), writable=ok_, why_not=why))
                if p == "/api/setup": ok_, why = settings_writable(self.client_address[0], self.headers); return self._json(200, dict(setup_view(c), writable=ok_, why_not=why))
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
            if p == "/api/settings":   # P1.7 / P4.1: the same sign-in as every other change, and without one only from your own network
                ok_, why = settings_writable(self.client_address[0], self.headers)
                if not ok_: return self._json(403, {"error": why})
                c = db()
                try: code, obj = save_settings(c, body, who or self.client_address[0])
                finally: c.close()
                return self._json(code, obj)
            if p == "/api/setup/test":
                name = str(body.get("name") or "")
                if not name: return self._json(400, {"error": "which app?"})
                ok_, note = test_connection(name); return self._json(200, {"ok": ok_, "note": note})
            if p == "/api/scan":
                if SCANNING["on"]: return self._json(200, {"ok": True, "note": "a scan is already running"})
                if FIXING["path"]: return self._json(200, {"ok": True, "note": "a fix is still comparing two big files; scan when it is done"})
                threading.Thread(target=scan, daemon=True).start(); return self._json(200, {"ok": True, "note": "scan started; the page updates itself"})
            if p == "/api/fix":
                path = str(body.get("path") or "")
                if not path: return self._json(400, {"error": "which file?"})
                c = db()
                try: code, obj = ui_fix(c, path, background=True)
                except Exception as e: note_error(f"fix failed for {path}: {e!r}"); return self._json(500, {"error": f"the fix stopped with an error and is written on the About tab: {e}"})   # 2026-11-14: a press answered 'Failed to fetch' and nothing said why
                finally: c.close()
                if not obj.get("dry_run"): log(f"fix {obj.get('kind', '')} -> {'started' if code == 202 else code} for {path}")
                return self._json(code, obj)
            if p == "/api/fix_all":
                paths = body.get("paths")
                if not isinstance(paths, list) or not paths or not all(isinstance(x, str) and x for x in paths): return self._json(400, {"error": "which files?"})
                c = db()
                try: code, obj = ui_fix_all(c, paths)
                except Exception as e: note_error(f"move all failed: {e!r}"); return self._json(500, {"error": f"the move stopped with an error and is written on the About tab: {e}"})
                finally: c.close()
                if code == 200: log(f"fix all -> {obj['done']} moved, {len(obj['skipped'])} left alone")
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
    while True:
        try: hh, mm = (int(x) for x in SCAN_TIME.split(":"))   # read each night: the Settings tab can change it
        except ValueError: hh, mm = 4, 30
        now = datetime.datetime.now(); nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now: nxt += datetime.timedelta(days=1)
        wait = (nxt - now).total_seconds()
        if wait > 60: time.sleep(60); continue   # look again every minute, so a new time from the Settings tab counts tonight
        time.sleep(max(1, wait))
        try: scan()
        except Exception: pass
        try: send_digest()   # also after a failed scan: the message then says so
        except Exception as e: note_error("the morning message failed: " + repr(e))
        time.sleep(61)   # never twice for one night

def main():
    ap = argparse.ArgumentParser(description=f"{APP}: {TAGLINE}")
    ap.add_argument("--daemon", action="store_true", help="serve the page and scan on a schedule")
    ap.add_argument("--scan", action="store_true", help="scan once and print the summary")
    ap.add_argument("--json", action="store_true", help="print the full state as JSON after --scan")
    ap.add_argument("--digest", action="store_true", help="print the morning message; with --send, also push it to NTFY_URL")
    ap.add_argument("--send", action="store_true", help="with --digest: send it now")
    a = ap.parse_args()
    try: load_settings()   # P1.7: what the Settings and Setup tabs saved (the container's settings still win)
    except Exception as e: log(f"settings: could not load the saved settings ({e}); the container's and the defaults apply")
    if a.digest:
        c = db(); d = digest(c); print(d["title"] + "\n" + d["text"])
        if a.send: print("sent" if ntfy(d["title"], d["text"], d["priority"], d["click"]) else "not sent (NTFY_URL is not set, or the topic refused it)")
        c.close(); return 0
    if a.scan:
        r = scan(); c = db(); s = state(c); c.close()
        if a.json: print(json.dumps(s, indent=1, default=str))
        else:
            print(s["lead"])
            for code, n in sorted(s["counts"].items(), key=lambda x: -x[1]): print(f"  {LABEL[code]:<26} {n:>6}   {s['wasted_by_h'][code]:>9} wasted")
        return 0 if r else 1
    if a.daemon:
        lock_sweep()   # markers left from before a restart mean nothing now
        threading.Thread(target=scheduler, daemon=True).start(); serve(); return 0
    ap.print_help(); return 2

if __name__ == "__main__": sys.exit(main())
