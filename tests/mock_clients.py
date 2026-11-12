"""A fake Deluge, NZBGet, qBittorrent, Sonarr and Radarr in one small HTTP server, answering in the shapes the real ones use
(recorded from a real server on 2026-11-12). start(world, port) -> server; world = {"deluge": [torrents], "nzbget": [history], "qbit": [torrents], "arr": {app: [history records]}}.
Each torrent: {"id", "name", "save_path", "files": [relative paths], "state", "ratio", "seeding_time", "label", "finished"}."""
import json, threading, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import os, random

def extra_files(DATA, blob):
    """Three situations only a client or an arr can explain, added to the fake disk: a software torrent, a finished usenet download, one still downloading."""
    return {"soft": blob(f"{DATA}/torrents/software/Some.App.2026/Some.App.2026.iso", 2, "someapp"),
            "usenet": blob(f"{DATA}/completed/tv/Show.S01E01.1080p.WEB/Show.S01E01.1080p.WEB.mkv", 2, "usenet-show"),
            "usenet_new": blob(f"{DATA}/completed/tv/Fresh.S01E02.1080p.WEB/Fresh.S01E02.1080p.WEB.mkv", 2, "usenet-fresh")}

def world_for(P, DATA):
    """What the fake clients and arrs know about the fake disk (P = paths from fake_disk.build + extra_files)."""
    rel = lambda p: os.path.relpath(p, os.path.dirname(os.path.dirname(p)))
    return {"deluge": [
        {"id": "dup1", "name": "Dune.Part.Two.2024.2160p.WEB-DL", "save_path": f"{DATA}/torrents/movies", "files": [os.path.basename(P["dup_dl"])], "state": "Seeding", "ratio": 2.5, "seeding_time": 30 * 86400, "label": "movies"},
        {"id": "hdr1", "name": "Frieren.S01E01.1080p.BluRay", "save_path": f"{DATA}/torrents/anime", "files": [os.path.basename(P["hdr_dl"])], "state": "Seeding", "ratio": 0.02, "seeding_time": 2 * 86400, "label": "cleanuparr-unlinked"},
        {"id": "scary", "name": "Scary.Movie.2026.1080p.BluRay.x264-EniaHD", "save_path": f"{DATA}/torrents/movies", "files": [os.path.basename(P["other_dl"])], "state": "Seeding", "ratio": 1.31, "seeding_time": 3 * 86400, "label": "movies"},
        {"id": "soft", "name": "Some.App.2026", "save_path": f"{DATA}/torrents/software", "files": [rel(P["soft"])], "state": "Seeding", "ratio": 0.4, "seeding_time": 2 * 86400, "label": ""}],
        "nzbget": [{"id": 7, "name": "Show.S01E01.1080p.WEB", "dir": os.path.dirname(P["usenet"]), "label": "tv"}],
        "nzbget_queue": [{"id": 8, "name": "Fresh.S01E02.1080p.WEB", "dir": os.path.dirname(P["usenet_new"]), "label": "tv"}],
        "qbit": [],
        "arr": {"sonarr": [{"eventType": "downloadFolderImported", "date": "2026-10-14T03:00:00Z", "sourceTitle": "Shogun.S01E05.1080p.WEB.h264-GRP", "data": {"droppedPath": P["unmatched"], "importedPath": f"{DATA}/media/tv/Shogun/Season 01/Shogun - S01E05 - Broken to the Fist WEBDL-1080p.mkv"}},
                           {"eventType": "downloadFolderImported", "date": "2026-11-01T03:00:00Z", "sourceTitle": "Show.S01E01.1080p.WEB", "data": {"droppedPath": P["usenet"], "importedPath": f"{DATA}/media/tv/Show/Season 01/Show - S01E01.mkv"}}],
                "radarr": []}}

def env_for(M):
    """The environment that points Dedupearr at a mock listening at M."""
    return dict(DELUGE_URL=f"{M}/deluge", DELUGE_PASSWORD="test", NZBGET_URL=f"{M}/nzbget", NZBGET_USER="test", NZBGET_PASSWORD="test", QBIT_URL=f"{M}/qbit", QBIT_USER="test", QBIT_PASSWORD="test",
                SONARR_URL=f"{M}/sonarr", SONARR_APIKEY="test", RADARR_URL=f"{M}/radarr", RADARR_APIKEY="test")

def _mangle(obj, rnd, depth=0):
    """One random wound somewhere in a reply: a key gone, a null, a wrong type, a huge string, odd unicode."""
    if isinstance(obj, dict) and obj:
        k = rnd.choice(list(obj)); op = rnd.choice(["drop", "null", "str", "num", "list", "recurse", "recurse", "huge"])
        if op == "drop": obj.pop(k)
        elif op == "null": obj[k] = None
        elif op == "str": obj[k] = "nope \u202e"
        elif op == "num": obj[k] = -1
        elif op == "list": obj[k] = []
        elif op == "huge": obj[k] = "x" * 20000
        elif depth < 4: _mangle(obj[k], rnd, depth + 1)
    elif isinstance(obj, list) and obj:
        i = rnd.randrange(len(obj)); op = rnd.choice(["recurse", "recurse", "drop", "wrong"])
        if op == "drop": obj.pop(i)
        elif op == "wrong": obj[i] = rnd.choice([None, 3, "x", {}])
        elif depth < 4: _mangle(obj[i], rnd, depth + 1)
    return obj

def start(world, port=0, host="127.0.0.1", mangle_seed=None):
    rnd = random.Random(mangle_seed) if mangle_seed is not None else None
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, obj, code=200, raw=None):
            if rnd is not None and raw is None and obj is not None and rnd.random() < 0.6: obj = _mangle(json.loads(json.dumps(obj)), rnd)
            b = raw if raw is not None else json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
            if getattr(self, "_cookie", None): self.send_header("Set-Cookie", self._cookie)
            self.end_headers(); self.wfile.write(b)
        def _body(self):
            n = int(self.headers.get("Content-Length") or 0); return self.rfile.read(n) if n else b""
        def do_GET(self):
            u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
            if u.path.startswith("/qbit/api/v2/torrents/info"): return self._send([{"hash": t["id"], "name": t["name"], "save_path": t["save_path"], "state": t["state"], "ratio": t["ratio"], "seeding_time": t["seeding_time"], "category": t.get("label", ""), "progress": 1.0 if t.get("finished", True) else 0.5} for t in world.get("qbit", [])])
            if u.path.startswith("/qbit/api/v2/torrents/files"):
                t = next((t for t in world.get("qbit", []) if t["id"] == q.get("hash", [""])[0]), None); return self._send([{"name": f, "size": 1, "progress": 1} for f in (t["files"] if t else [])])
            for app in ("sonarr", "radarr", "whisparr", "sportarr", "lidarr"):
                if u.path == f"/{app}/api/v3/history":
                    if self.headers.get("X-Api-Key") != "test": return self._send({"error": "Unauthorized"}, 401)
                    recs = world.get("arr", {}).get(app, []); page = int(q.get("page", ["1"])[0]); size = int(q.get("pageSize", ["500"])[0])
                    return self._send({"page": page, "pageSize": size, "totalRecords": len(recs), "records": recs[(page - 1) * size: page * size]})
                if u.path == f"/{app}/api/v3/system/status": return self._send({"appName": app, "version": "4.0.0"})
            self._send({"error": "not found"}, 404)
        def do_POST(self):
            u = urllib.parse.urlparse(self.path); raw = self._body()
            if u.path == "/deluge/json":
                r = json.loads(raw or b"{}"); m = r.get("method"); p = r.get("params") or []; res = None
                if m == "auth.login": res = (p[0] == "test"); self._cookie = "_session_id=abc; Path=/"
                elif m == "web.connected": res = world.setdefault("_deluge_connected", False)
                elif m == "web.get_hosts": res = [["ae16eb61ae16eb61", "127.0.0.1", 58846, "localclient"]]
                elif m == "web.connect": world["_deluge_connected"] = True; res = ["core.add_torrent_file"]
                elif m == "core.get_torrents_status":
                    res = {t["id"]: {"name": t["name"], "save_path": t["save_path"], "state": t["state"], "ratio": t["ratio"], "seeding_time": t["seeding_time"], "time_added": 1783706283, "label": t.get("label", ""), "total_size": 1,
                                     "is_finished": t.get("finished", True), "progress": 100.0 if t.get("finished", True) else 50.0, "files": [{"index": i, "path": f, "size": 1, "offset": 0} for i, f in enumerate(t["files"])]} for t in world.get("deluge", [])}
                elif m == "core.remove_torrent": world.setdefault("_removed", []).append((p[0], p[1])); world["deluge"] = [t for t in world["deluge"] if t["id"] != p[0]]; res = True
                else: return self._send({"result": None, "error": {"message": f"unknown method {m}", "code": 2}, "id": r.get("id")})
                return self._send({"result": res, "error": None, "id": r.get("id")})
            if u.path == "/nzbget/jsonrpc":
                if self.headers.get("Authorization") != "Basic dGVzdDp0ZXN0": return self._send({}, 401, b"")   # test:test
                r = json.loads(raw or b"{}"); m = r.get("method")
                if m == "history": return self._send({"version": "1.1", "result": [{"NZBID": h["id"], "Name": h["name"], "Status": h.get("status", "SUCCESS/ALL"), "Category": h.get("label", ""), "FinalDir": h["dir"], "DestDir": h["dir"], "MarkStatus": "NONE", "FileSizeMB": 1, "HistoryTime": 1783706283, "Kind": "NZB"} for h in world.get("nzbget", [])]})
                if m == "listgroups": return self._send({"version": "1.1", "result": [{"NZBID": g["id"], "NZBName": g["name"], "DestDir": g["dir"], "Category": g.get("label", "")} for g in world.get("nzbget_queue", [])]})
                if m == "config": return self._send({"version": "1.1", "result": [{"Name": "MainDir", "Value": "/data"}, {"Name": "DestDir", "Value": "/data/completed"}]})
                if m == "editqueue": world.setdefault("_nz_edits", []).append(r.get("params")); return self._send({"version": "1.1", "result": True})
                return self._send({"version": "1.1", "error": {"name": "JSONRPCError", "code": -32601, "message": "Method not found"}})
            if u.path == "/qbit/api/v2/auth/login":
                f = urllib.parse.parse_qs(raw.decode()); ok = f.get("username", [""])[0] == "test" and f.get("password", [""])[0] == "test"
                self._cookie = "SID=xyz; Path=/"; return self._send(None, 200 if ok else 403, b"Ok." if ok else b"Fails.")
            if u.path == "/qbit/api/v2/torrents/delete": f = urllib.parse.parse_qs(raw.decode()); world.setdefault("_removed", []).append((f.get("hashes", [""])[0], f.get("deleteFiles", [""])[0])); return self._send(None, 200, b"")
            self._send({"error": "not found"}, 404)
    srv = ThreadingHTTPServer((host, port), H); threading.Thread(target=srv.serve_forever, daemon=True).start(); return srv

if __name__ == "__main__":   # python3 mock_clients.py <port> [fake disk root]: the demo runs this beside the app so its downloads have a client and an arr behind them
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import fake_disk
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765; root = sys.argv[2] if len(sys.argv) > 2 else ""
    if root:
        P = {k: v for k, v in fake_disk.build.__globals__.get("_last", {}).items()} if False else None
        P = fake_disk.build(root) if not os.path.exists(os.path.join(root, "media")) else fake_disk.paths(root)
        P.update(extra_files(root, fake_disk.blob)); w = world_for(P, root)
    else: w = {}
    s = start(w, port, "0.0.0.0" if root else "127.0.0.1"); print("mock clients on", s.server_address, "with" if root else "without", "a world"); threading.Event().wait()
