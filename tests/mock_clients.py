"""A fake Deluge, NZBGet, qBittorrent, Sonarr and Radarr in one small HTTP server, answering in the shapes the real ones use
(recorded from a real server on 2026-11-12). start(world, port) -> server; world = {"deluge": [torrents], "nzbget": [history], "qbit": [torrents], "arr": {app: [history records]}}.
Each torrent: {"id", "name", "save_path", "files": [relative paths], "state", "ratio", "seeding_time", "label", "finished"}."""
import json, threading, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

def start(world, port=0, host="127.0.0.1"):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, obj, code=200, raw=None):
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

if __name__ == "__main__":
    import sys
    s = start({}, int(sys.argv[1]) if len(sys.argv) > 1 else 8765); print("mock clients on", s.server_address); threading.Event().wait()
