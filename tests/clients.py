"""Phase 2: with the fake Deluge, NZBGet, qBittorrent and arrs answering, downloads get their real verdicts and the seeding rule decides
"can go now". Run: python tests/clients.py   Exit 1 on failure."""
import os, sys, json, time, tempfile, socket
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-clients-"); DATA = os.path.join(TMP, "data")
import fake_disk, mock_clients  # noqa: E402
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); M = f"http://127.0.0.1:{port}"
now = time.time(); P = fake_disk.build(DATA, now)
P.update(mock_clients.extra_files(DATA, fake_disk.blob)); world = mock_clients.world_for(P, DATA)
srv = mock_clients.start(world, port)
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100", SEED_MIN_RATIO="1.0", SEED_MIN_DAYS="14", SEED_RULE="both", CLIENT_TIMEOUT="5", **mock_clients.env_for(M))
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:240]}]"))
r = d.scan(now=now); c = d.db(); S = d.state(c); by = {i["path"]: i for i in S["items"]}
code = lambda k: by.get(P[k], {}).get("code")
check("paths() of an existing fake disk equals what build() returned (the demo relies on it)", {k: v for k, v in fake_disk.paths(DATA).items()} == {k: v for k, v in P.items() if k not in ("soft", "usenet", "usenet_new")}, "")
check("every client and arr was read", all(v["ok"] for v in d.CLIENT_STATUS.values()) and set(d.CLIENT_STATUS) == {"Deluge", "NZBGet", "qBittorrent", "Sonarr", "Radarr"}, d.CLIENT_STATUS)
check("identical copy seeding past the rule: Stored twice, can go now", code("dup_dl") == "DUPLICATE_COPY" and by[P["dup_dl"]]["removable"] and "can go now" in by[P["dup_dl"]]["plain"], by.get(P["dup_dl"], {}).get("plain"))
check("header-differs copy still seeding: Stored twice, header differs, not removable, the sentence names the rule", code("hdr_dl") == "DUPLICATE_HEADER" and not by[P["hdr_dl"]]["removable"] and "still seeding" in by[P["hdr_dl"]]["plain"] and "ratio 1" in by[P["hdr_dl"]]["plain"], by.get(P["hdr_dl"], {}).get("plain", "")[-200:])
check("the download Sonarr imported, whose library file was replaced = Leftover, removable by nothing holding it? no: Deluge does not know it, so it can go", code("unmatched") == "LEFTOVER" and by[P["unmatched"]]["removable"], by.get(P["unmatched"]))
check("the leftover's evidence names the arr, the date and the replaced file", "Sonarr imported it on 2026-10-14" in by[P["unmatched"]]["evidence"] and "replaced" in by[P["unmatched"]]["evidence"], by[P["unmatched"]]["evidence"])
# categories (2026-11-12, owner request): from the arrs' own folders first, then the import, the client label, then folder names
cat = lambda k: by[P[k]]["category"]
check("a movie and the seed linked to it are Movies (Radarr's root folder, then the twin)", cat("real_lib") == "movies" and cat("real_seed") == "movies", (cat("real_lib"), cat("real_seed")))
check("Sonarr's anime flag wins: the library episode and its download copy are Anime", cat("hdr_lib") == "anime" and cat("hdr_dl") == "anime", (cat("hdr_lib"), cat("hdr_dl")))
check("the same title in Sonarr's standard folder is TV shows, not Anime", cat("look_lib") == "tv", cat("look_lib"))
check("a usenet download Sonarr imported is TV shows", by[P["usenet"]]["category"] == "tv", by[P["usenet"]]["category"])
check("a software torrent no arr knows is Other", by[P["soft"]]["category"] == "other", by[P["soft"]]["category"])
check("every row has a category and the totals add up", all(i["category"] in d.CAT_LABEL for i in S["items"]) and sum(cc["files"] for cc in S["categories"]) == len(S["items"]), S["categories"])
check("a release name never outvotes its folder: FLAC and Dual Audio anime in completed/tv is TV, not Music (seen on the owner's server 2026-11-12)",
      [d.category_of(x, {}) for x in ("/data/completed/tv/Demon.Slayer.S05E08.REPACK.1080p.FLAC.2.0.AVC.REMUX-X/a.mkv", "/data/completed/tv/Bleach.S13E33.Dual.Audio-ZR/b.mkv", "/data/torrents/music/Album/c.mkv")] == ["tv", "tv", "music"])
check("client labels and folder words map to categories", [d.words_category(t) for t in ("tv-sonarr", "Anime", "xxx", "radarr", "software", "")] == ["tv", "anime", "adult", "movies", None, None])
check("Scary Movie: different release, ratio reached but only 3 of 14 days = still seeding under the 'both' rule", code("other_dl") == "DOWNLOAD_OTHER_RELEASE" and not by[P["other_dl"]]["removable"] and "ratio 1 and 14 days" in by[P["other_dl"]]["plain"], by.get(P["other_dl"], {}).get("plain", "")[-160:])
check("a same-size lookalike that no client and no arr knows = Orphan (nothing holds it)", code("look_dl") == "ORPHAN" and by[P["look_dl"]]["wasted"] == by[P["look_dl"]]["size"] and by[P["look_dl"]]["removable"], by.get(P["look_dl"]))
check("software torrent seeding, never imported = Seeding only, not waste, not needs-you", code("soft") == "SEEDING_ONLY" and by[P["soft"]]["wasted"] == 0 and not by[P["soft"]]["needs_you"], by.get(P["soft"]))
check("usenet download imported by Sonarr, library file gone = Leftover with 'no seeding to wait for'", code("usenet") == "LEFTOVER" and "usenet" in by[P["usenet"]]["plain"] and by[P["usenet"]]["removable"], by.get(P["usenet"], {}).get("plain"))
check("a usenet download still in NZBGet's queue is not removable", code("usenet_new") in ("DOWNLOAD_UNMATCHED", "SEEDING_ONLY") and not by[P["usenet_new"]]["removable"], by.get(P["usenet_new"]))
check("evidence carries the client's state for every download", all("Deluge:" in i["evidence"] or "NZBGet:" in i["evidence"] or "not in any download client" in i["evidence"] for i in S["items"] if i["kind"] == "download"), [i["evidence"][-80:] for i in S["items"] if i["kind"] == "download" and "client" not in i["evidence"]][:2])
check("the lead says how much can go right now", "can go right now" in S["lead"] and S["now_bytes"] >= by[P["dup_dl"]]["size"], S["lead"])
check("reclaim counts leftovers and orphans, undecided counts the rest", S["reclaim_bytes"] >= by[P["unmatched"]]["size"] + by[P["dup_dl"]]["size"], (S["reclaim_bytes"], S["decide_bytes"]))
a = d.about(c)
check("About lists every connection and never a password", any("Deluge:" in h["text"] for h in a["health"]) and "test" not in json.dumps(a["settings"]["connections"]) and "password" not in json.dumps(a["settings"]).lower(), a["settings"]["connections"])
# a restart: the in-memory client status is gone, but the page must still say what the last scan learned (seen on production 2026-11-12: lead lost 'can go right now')
d.CLIENT_STATUS.clear(); S_r = d.state(c)
check("after a restart the lead still says how much can go right now", "can go right now" in S_r["lead"] and S_r["clients"], S_r["lead"][-120:])
d.CLIENT_STATUS.clear(); d.kv_set(c, "client_status", ""); S_u = d.state(c)   # upgraded from a build that never saved the status: rows with client knowledge are enough
check("after an upgrade without a saved status the lead still says how much can go right now", "can go right now" in S_u["lead"], S_u["lead"][-120:])
# a client that stops answering: the scan still completes and About says so
srv.shutdown(); srv.server_close(); d.CLIENT_STATUS.clear()
r2 = d.scan(now=now + 60); S2 = d.state(c); a2 = d.about(c)
check("with every client down the scan still finishes", r2 is not None and S2["last_run"]["note"] == "ok")
check("About names each client that could not be read", sum(1 for h in a2["health"] if "could not be read" in h["text"]) >= 3, [h["text"][:60] for h in a2["health"]])
check("with the arrs down, categories still come from folder names (an anime torrent folder stays Anime)", {i["path"]: i["category"] for i in S2["items"]}.get(P["hdr_dl"]) == "anime", [(i["path"][-40:], i["category"]) for i in S2["items"]][:6])
check("downloads fall back to the content-only verdicts, never a guess about seeding", all(i["code"] not in ("LEFTOVER", "ORPHAN", "SEEDING_ONLY") for i in S2["items"]), [i["code"] for i in S2["items"] if i["kind"] == "download"])
c.close(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
