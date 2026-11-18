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
check("paths() of an existing fake disk equals what build() returned (the demo relies on it)", {k: v for k, v in fake_disk.paths(DATA).items()} == {k: v for k, v in P.items() if k not in mock_clients.extra_files(DATA, lambda *a: a[0])}, "")
check("every client and arr was read", all(v["ok"] for v in d.CLIENT_STATUS.values()) and set(d.CLIENT_STATUS) == {"Deluge", "NZBGet", "qBittorrent", "rTorrent", "Transmission", "SABnzbd", "Sonarr", "Radarr"}, d.CLIENT_STATUS)
# P2.0 + P2.3 (2026-11-18): rTorrent, Transmission and SABnzbd have no address set here; Sonarr's Download Clients page gave them
check("rTorrent, Transmission and SABnzbd were found on Sonarr's Download Clients page, the others came from the container settings",
      all(d.CLIENT_STATUS[k].get("source") == "sonarr" for k in ("rTorrent", "Transmission", "SABnzbd")) and all(d.CLIENT_STATUS[k].get("source") == "container" for k in ("Deluge", "NZBGet", "qBittorrent")), {k: v.get("source") for k, v in d.CLIENT_STATUS.items()})
check("no write call ever reached rTorrent, Transmission or SABnzbd (the readers only read)", "_writes" not in world, world.get("_writes"))
check("rTorrent: a torrent seeding that no arr imported = Seeding only, with rTorrent's state and its ratio from thousandths", code("rt_seed") == "SEEDING_ONLY" and "rTorrent: seeding, ratio 2.50, 40 days" in by[P["rt_seed"]]["client_text"], by.get(P["rt_seed"], {}).get("client_text"))
check("rTorrent: a public torrent is marked public, a private one private", by[P["rt_seed"]]["client"]["private"] is False and by[P["rt_left"]]["client"]["private"] is True)
check("rTorrent: a leftover past the rule can go, but there is no button: Dedupearr cannot remove torrents from rTorrent, and the row says to remove it there first",
      code("rt_left") == "LEFTOVER" and by[P["rt_left"]]["removable"] and not by[P["rt_left"]]["fixable"] and by[P["rt_left"]]["wait"] == "Remove it in rTorrent first (keep its files)", (by.get(P["rt_left"], {}).get("fixable"), by.get(P["rt_left"], {}).get("wait")))
check("Transmission: a torrent 40% done = Still downloading, not waste", code("tr_dl") == "DOWNLOADING" and by[P["tr_dl"]]["wasted"] == 0 and "Transmission" in by[P["tr_dl"]]["plain"], by.get(P["tr_dl"], {}).get("plain"))
check("Transmission: both files of a private two-file torrent are known (files under a folder), seeding only, and the label comes from its labels",
      code("tr_pack1") == code("tr_pack2") == "SEEDING_ONLY" and by[P["tr_pack1"]]["client"]["private"] is True and by[P["tr_pack1"]]["client"]["id"] == by[P["tr_pack2"]]["client"]["id"], (code("tr_pack1"), code("tr_pack2")))
check("Transmission: a torrent that is done counts as finished although Transmission's own isFinished says false (it means 'stopped at its limit')", by[P["tr_pack1"]]["client"]["finished"] is True)
check("SABnzbd: a finished job Radarr imported and the library moved on from = Leftover, no seeding to wait for, can go now",
      code("sab_done") == "LEFTOVER" and by[P["sab_done"]]["removable"] and by[P["sab_done"]]["fixable"] and "usenet" in by[P["sab_done"]]["plain"], by.get(P["sab_done"], {}).get("plain"))
check("SABnzbd: a failed job nobody imported is a download to decide on, never seeding, and its sentence names SABnzbd",
      code("sab_failed") == "DOWNLOAD_UNMATCHED" and "SABnzbd marked it failed" in by[P["sab_failed"]]["plain"] and "seeding it" not in by[P["sab_failed"]]["plain"] and by[P["sab_failed"]]["removable"], by.get(P["sab_failed"], {}).get("plain"))
check("SABnzbd's API key was asked for by name, never sent in the clear to the page", "test" not in json.dumps(d.state(c)["clients"]))
dz = d.discovery()
check("Radarr's switched-off rTorrent is not read (Sonarr's switched-on one is), and a kind Dedupearr cannot read is named on About",
      any("cannot read yet" in n and "Flood" in n for n in dz["notes"]) and not any("rTorrent" in n and "switched off" in n for n in dz["notes"]), dz["notes"])
check("what the arrs list is kept without user names or passwords", dz["found"] and all("user" not in f and "password" not in json.dumps(f).lower().replace("passwords", "") for f in dz["found"]), dz["found"][:1])
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
check("a usenet download still in NZBGet's queue is not removable", code("usenet_new") in ("DOWNLOAD_UNMATCHED", "SEEDING_ONLY", "DOWNLOADING") and not by[P["usenet_new"]]["removable"], by.get(P["usenet_new"]))
# #20 (raised in #18): a download still coming in is "Still downloading", never "Seeding only" with torrent words
check("a usenet download still in NZBGet's queue = Still downloading, not waste, not needs-you, no seeding or torrent words",
      code("usenet_new") == "DOWNLOADING" and by[P["usenet_new"]]["wasted"] == 0 and not by[P["usenet_new"]]["needs_you"] and not any(w in by[P["usenet_new"]]["plain"].lower() for w in ("seed", "torrent")), by.get(P["usenet_new"], {}).get("plain"))
check("a finished download Sonarr is about to import = Waiting for import, not waste, names Sonarr",
      code("await_imp") == "AWAITING_IMPORT" and by[P["await_imp"]]["wasted"] == 0 and not by[P["await_imp"]]["needs_you"] and not by[P["await_imp"]]["removable"] and "Sonarr" in by[P["await_imp"]]["plain"], by.get(P["await_imp"], {}).get("plain"))
check("a finished download Radarr cannot import says so, gives Radarr's reason and where to fix it, and is never 'can go'",
      code("blocked_imp") == "AWAITING_IMPORT" and "cannot import it on its own" in by[P["blocked_imp"]]["plain"] and "Manual Import required" in by[P["blocked_imp"]]["plain"] and "Activity queue" in by[P["blocked_imp"]]["plain"] and not by[P["blocked_imp"]]["removable"], by.get(P["blocked_imp"], {}).get("plain"))
check("a failed usenet job nobody imported is never 'Seeding only': it is a download to decide on, counted in full, needs you, can go now (seen on a real server 2026-11-12)",
      code("usenet_failed") == "DOWNLOAD_UNMATCHED" and by[P["usenet_failed"]]["wasted"] == by[P["usenet_failed"]]["size"] and by[P["usenet_failed"]]["needs_you"] and by[P["usenet_failed"]]["removable"], by.get(P["usenet_failed"]))
check("the same, for a failed-job file whose size nothing in the library shares", code("usenet_failed2") == "DOWNLOAD_UNMATCHED" and by[P["usenet_failed2"]]["wasted"] == by[P["usenet_failed2"]]["size"] and by[P["usenet_failed2"]]["needs_you"], by.get(P["usenet_failed2"]))
check("its sentence says usenet does not seed and that NZBGet marked it failed", "does not seed" in by[P["usenet_failed"]]["plain"] and "failed" in by[P["usenet_failed"]]["plain"] and "seeding it" not in by[P["usenet_failed"]]["plain"], by[P["usenet_failed"]]["plain"])
check("the undecided total includes both failed usenet files", S["decide_bytes"] >= by[P["usenet_failed"]]["size"] + by[P["usenet_failed2"]]["size"], (S["decide_bytes"], by[P["usenet_failed"]]["size"]))
check("evidence carries the client's state for every download", all(any(k + ":" in i["evidence"] for k in d.CLIENTS) or "not in any download client" in i["evidence"] for i in S["items"] if i["kind"] == "download"), [i["evidence"][-80:] for i in S["items"] if i["kind"] == "download" and "client" not in i["evidence"]][:2])
check("the lead says how much can go right now", "can go right now" in S["lead"] and S["now_bytes"] >= by[P["dup_dl"]]["size"], S["lead"])
check("reclaim counts leftovers and orphans, undecided counts the rest", S["reclaim_bytes"] >= by[P["unmatched"]]["size"] + by[P["dup_dl"]]["size"], (S["reclaim_bytes"], S["decide_bytes"]))
a = d.about(c)
check("About lists every connection and never a password", any("Deluge:" in h["text"] for h in a["health"]) and "test" not in json.dumps(a["settings"]["connections"]) and "password" not in json.dumps(a["settings"]).lower(), a["settings"]["connections"])
# a restart: the in-memory client status is gone, but the page must still say what the last scan learned (seen on production 2026-11-12: lead lost 'can go right now')
d.CLIENT_STATUS.clear(); S_r = d.state(c)
check("after a restart the lead still says how much can go right now", "can go right now" in S_r["lead"] and S_r["clients"], S_r["lead"][-120:])
d.CLIENT_STATUS.clear(); d.kv_set(c, "client_status", ""); S_u = d.state(c)   # upgraded from a build that never saved the status: rows with client knowledge are enough
check("after an upgrade without a saved status the lead still says how much can go right now", "can go right now" in S_u["lead"], S_u["lead"][-120:])
# 2026-11-13 (owner: "some are private but most are public"): the rule follows each torrent's private flag, and a row without a button says when it can go
ci_h = by[P["hdr_dl"]].get("client") or {}; ci_d = by[P["dup_dl"]].get("client") or {}
check("the Deluge reader carries each torrent's private flag", ci_h.get("private") is True and ci_d.get("private") is False, (ci_h.get("private"), ci_d.get("private")))
old_rule = d.SEED_RULE
try:
    d.SEED_RULE = "auto"
    pub = dict(client="Deluge", finished=True, ratio=0.02, seeding_days=15, private=False); prv = dict(pub, private=True); unk = dict(pub, private=None)
    check("auto: a public torrent past 14 days can go even at a low ratio", d.seeding_done(pub) and d.seed_wait(pub) == "")
    check("auto: a private torrent past 14 days still waits for ratio 1, and the row says so", not d.seeding_done(prv) and d.seed_wait(prv) == "Still seeding (private) · can go once ratio reaches 1", d.seed_wait(prv))
    check("auto: a client that does not say is treated as private (the safe side)", not d.seeding_done(unk) and "does not say" in d.seed_sentence(unk), d.seed_sentence(unk))
    young = dict(pub, seeding_days=10.2)
    check("auto: a young public torrent says when it can go", d.seed_wait(young) == "Still seeding · can go in about 4 days, or sooner at ratio 1", d.seed_wait(young))
    check("auto: the sentence says public and the rule it used", "a public torrent" in d.seed_sentence(young) and "ratio 1 or 14 days" in d.seed_sentence(young), d.seed_sentence(young))
    d.SEED_RULE = "both"
    check("SEED_RULE=both still holds every torrent to ratio and days", not d.seeding_done(pub) and d.rule_for(pub) == "both")
    d.SEED_RULE = "either"
    check("SEED_RULE=either lets a private one go on days alone", d.seeding_done(prv))
finally: d.SEED_RULE = old_rule
check("a waiting row carries why there is no button; a row with a button carries none", by[P["hdr_dl"]]["wait"].startswith("Still seeding") and not by[P["hdr_dl"]]["fixable"] and all(not i["wait"] for i in S["items"] if i["fixable"]), by[P["hdr_dl"]].get("wait"))
# P2.0: a client the arrs list but have switched off. Not running: a quiet note, never a fault (the staged update would refuse a build that calls it broken).
# Running anyway: it is read, because its files are still seeding and must not look like orphans.
saved_dc = json.loads(json.dumps(world["downloadclients"])); tr = next(e for e in world["downloadclients"]["sonarr"] if e["implementation"] == "Transmission"); tr["enable"] = False
d.CLIENT_STATUS.clear(); d.scan(now=now + 30); S3 = d.state(c); by3 = {i["path"]: i for i in S3["items"]}
check("a client switched off in the arr but still running is read, and About says so", d.CLIENT_STATUS.get("Transmission", {}).get("ok") and "switched off in Sonarr, but still running" in d.CLIENT_STATUS["Transmission"]["note"] and by3[P["tr_dl"]]["code"] == "DOWNLOADING", d.CLIENT_STATUS.get("Transmission"))
next(f for f in tr["fields"] if f["name"] == "port")["value"] = 9
d.CLIENT_STATUS.clear(); d.scan(now=now + 40); S3 = d.state(c); a3 = d.about(c)
check("a client switched off in the arr and not running is a quiet note, not a fault, and the scan is fine", "Transmission" not in d.CLIENT_STATUS and S3["last_run"]["note"] == "ok" and any("Transmission is switched off in Sonarr" in h["text"] and h["level"] == "ok" for h in a3["health"]) and not any("could not be read" in h["text"] for h in a3["health"]), [h["text"][:70] for h in a3["health"]])
rt = next(e for e in world["downloadclients"]["sonarr"] if e["implementation"] == "RTorrent"); rt["enable"] = False; next(f for f in rt["fields"] if f["name"] == "port")["value"] = 9
read_, soft_, notes_ = d.plan_clients(d.read_arr_clients(), d.CLIENTS)
check("plan: an address set here always wins over the arr's; a kind only switched off everywhere is asked softly", read_["Deluge"][1] == "container" and "rTorrent" in soft_ and "rTorrent" not in read_, (list(read_), list(soft_)))
key = d.CLIENTS["SABnzbd"]["key"]; d.CLIENTS["SABnzbd"]["key"] = ""
d.CLIENT_STATUS.clear(); d.scan(now=now + 50); d.CLIENTS["SABnzbd"]["key"] = key
check("a client found in an arr that needs its key says the arr does not share keys and where to set it", not d.CLIENT_STATUS["SABnzbd"]["ok"] and "never share passwords or API keys" in d.CLIENT_STATUS["SABnzbd"]["note"], d.CLIENT_STATUS.get("SABnzbd"))
world["downloadclients"] = saved_dc; d.CLIENT_STATUS.clear(); d.scan(now=now + 60)
# a client that stops answering: the scan still completes and About says so
srv.shutdown(); srv.server_close(); d.CLIENT_STATUS.clear()
r2 = d.scan(now=now + 60); S2 = d.state(c); a2 = d.about(c)
check("with every client down the scan still finishes", r2 is not None and S2["last_run"]["note"] == "ok")
check("About names each client that could not be read", sum(1 for h in a2["health"] if "could not be read" in h["text"]) >= 3, [h["text"][:60] for h in a2["health"]])
check("with the arrs down, categories still come from folder names (an anime torrent folder stays Anime)", {i["path"]: i["category"] for i in S2["items"]}.get(P["hdr_dl"]) == "anime", [(i["path"][-40:], i["category"]) for i in S2["items"]][:6])
check("downloads fall back to the content-only verdicts, never a guess about seeding", all(i["code"] not in ("LEFTOVER", "ORPHAN", "SEEDING_ONLY") for i in S2["items"]), [i["code"] for i in S2["items"] if i["kind"] == "download"])
c.close(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
