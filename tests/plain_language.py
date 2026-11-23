"""Every sentence a person reads is plain: no raw codes, no paths in the sentence itself, ends with a full stop, says what happens next.
Run: python tests/plain_language.py   Exit 1 on failure."""
import os, re, sys, tempfile, atexit, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
os.environ.update(CONFIG_DIR=tempfile.mkdtemp(prefix="dedupearr-pl-"), SCAN_ROOTS="/nonexistent")
atexit.register(shutil.rmtree, os.environ["CONFIG_DIR"], ignore_errors=True)   # removed when the test ends (#36)
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:200]}]"))
CODE = re.compile(r"\b[A-Z]{3,}_[A-Z_]{3,}\b")
for code, label in d.LABEL.items():
    v = {"code": code, "size": 4_200_000_000, "twin": "/data/media/movies/Dune Part Two (2024)/Dune.Part.Two.2024.2160p.WEB-DL.mkv"}
    s = d.sentence(v)
    check(f"{code}: no raw code in the sentence or label", not CODE.search(s) and not CODE.search(label), s)
    check(f"{code}: ends with a full stop", s.endswith("."), s)
    check(f"{code}: no raw path in the sentence", "/data/" not in s and ".mkv" not in s, s)
    check(f"{code}: label is short title case words", 2 <= len(label) <= 28 and label[0].isupper(), label)
check("waste sentences say how much", "4.2 GB" in d.sentence({"code": "DUPLICATE_COPY", "size": 4_200_000_000, "twin": None}) and "4.2 GB" in d.sentence({"code": "BIN_OLD", "size": 4_200_000_000}))
check("the unmatched sentence says who decides next", "phase 2" in d.sentence({"code": "DOWNLOAD_UNMATCHED", "size": 1, "twin": None}))
for st, want in (("failed", "marked it failed"), ("finished", "finished it"), ("deleted/manual", "lists it as deleted, manual")):   # a finished usenet job nobody imported (seen on a real server 2026-11-12)
    s = d.sentence({"code": "DOWNLOAD_UNMATCHED", "size": 11_300_000_000, "twin": None, "client": {"client": "NZBGet", "state": st, "finished": True}})
    check(f"usenet job NZBGet {st}: says so, says usenet does not seed, never says seeding or torrent, ends with a full stop", want in s and "does not seed" in s and "11.3 GB" in s and "seeding it" not in s and "torrent" not in s and s.endswith(".") and not CODE.search(s), s)
s = d.sentence({"code": "DOWNLOAD_UNMATCHED", "size": 2_000_000_000, "twin": None, "client": {"client": "SABnzbd", "usenet": True, "state": "failed", "finished": True}})   # P2.3
check("usenet job SABnzbd failed: names SABnzbd, says usenet does not seed, never torrent words", "SABnzbd marked it failed" in s and "does not seed" in s and "torrent" not in s and s.endswith("."), s)
for cl in ("rTorrent", "Transmission"):
    s = d.no_remove_sentence({"client": cl})
    check(f"{cl} cannot be told to remove: the sentence says so, says what to do there and what happens next", f"remove the torrent in {cl}".lower() in s.lower() and "keep its files" in s and "next scan" in s and s.endswith(".") and not CODE.search(s), s)
# P2.5: the two Plex sentences, word for word
check("Outside Plex's folders: says what it is, what would change it, and that nothing is wasted",
      d.sentence({"code": "NOT_IN_PLEX_FOLDER", "size": 1, "twin": None}) == "In your library, but in a folder no Plex library looks in, so Plex never shows it. Add this folder to a Plex library, or move the file into a folder Plex already has, and it appears. No space is wasted; press Ignore if this folder is meant to stay out of Plex.", d.sentence({"code": "NOT_IN_PLEX_FOLDER", "size": 1, "twin": None}))
check("Plex does not show it: says the usual reasons and what to do",
      d.sentence({"code": "NOT_IN_PLEX", "size": 1, "twin": None, "path": "/data/media/movies/Odd Name Film/odd_name_film_final_v2.mkv"}) == "In a folder Plex looks in, but Plex does not show this file. Usually Plex cannot match the name, or has not scanned since the file arrived. Rename it the way Plex expects, or run a library scan in Plex; once Plex lists it, this note goes away. No space is wasted.", d.sentence({"code": "NOT_IN_PLEX", "size": 1, "twin": None, "path": "/data/media/movies/Odd Name Film/odd_name_film_final_v2.mkv"}))
check("Plex does not show it, for a trailer: says that is expected",
      d.sentence({"code": "NOT_IN_PLEX", "size": 1, "twin": None, "path": "/data/media/movies/Heat (1995)/Trailers/Heat.1995.Trailer.mkv"}) == "In a folder Plex looks in, but Plex does not show this file. The name says it is an extra (a trailer, sample, menu or bonus clip), which Plex does not list on its own, so that is expected. Rename it the way Plex expects, or run a library scan in Plex; once Plex lists it, this note goes away. No space is wasted.", d.sentence({"code": "NOT_IN_PLEX", "size": 1, "twin": None, "path": "/data/media/movies/Heat (1995)/Trailers/Heat.1995.Trailer.mkv"}))
check("Outside Plex's folders needs a person, Plex does not show it is a quiet note, and neither has a fix", "NOT_IN_PLEX_FOLDER" in d.NEEDS_YOU and "NOT_IN_PLEX" not in d.NEEDS_YOU and not set(d.PLEX_CODES) & (set(d.FIXABLE) | set(d.RECLAIM)))
check("sizes read like a person says them", d.human(0) == "0 B" and d.human(999) == "999 B" and d.human(48_200_000_000) == "48.2 GB" and d.human(1_000_000_000_000) == "1 TB", [d.human(x) for x in (0, 999, 48_200_000_000, 1_000_000_000_000)])
check("friendly title: scene name", d.friendly_title("/x/Dune.Part.Two.2024.2160p.UHD.BluRay.x265-GRP.mkv") == "Dune Part Two 2024")
check("friendly title: keeps the episode number", d.friendly_title("/x/Shogun.S01E05.1080p.WEB.h264-GRP.mkv") == "Shogun S01E05")
check("friendly title: plain names untouched", d.friendly_title("/x/Heat (1995)/Heat 1995.mkv") == "Heat 1995")
check("friendly title: arr tags dropped", d.friendly_title("/x/Scary Movie (2026) {tmdb-1273221} - {edition-Theatrical Cut} [Bluray-1080p][EAC3 7.1][x264]-playHD.mkv") == "Scary Movie (2026)", d.friendly_title("/x/Scary Movie (2026) {tmdb-1273221} - {edition-Theatrical Cut} [Bluray-1080p][EAC3 7.1][x264]-playHD.mkv"))
check("title key: scene name and arr name agree", d.title_key("/x/Scary.Movie.2026.1080p.BluRay.x264-EniaHD.mkv") == d.title_key("/x/Scary Movie (2026) {tmdb-1273221} - {edition-Theatrical Cut} [Bluray-1080p][EAC3 7.1][x264]-playHD.mkv") == "scary movie 2026")
check("title key: episodes", d.title_key("/x/Shogun.S01E05.1080p.WEB.mkv") == "shogun s01e05" and d.same_title(d.title_key("/x/Isekai Maou - E04 [BD 1080p].mkv"), d.title_key("/x/Isekai Maou (2018) - S01E04 - Name.mkv")) and d.same_title(d.title_key("/x/Bleach.S17E46.1080p.mkv"), d.title_key("/x/Bleach (2004) - S17E46 - 412 - THE END.mkv")))
check("title key: different years or episodes do not match", not d.same_title(d.title_key("/x/Heat.1995.mkv"), d.title_key("/x/Heat.2024.mkv")) and not d.same_title("shogun s01e05", "shogun s01e06"))
check("classify: recycle wins over library", d.classify("/data/media/.trash/tv/x.mkv") == "recycle")
check("classify: downloads", d.classify("/data/torrents/movies/x.mkv") == "download" and d.classify("/mnt/user/downloads/complete/x.mkv") == "download")
check("classify: library", d.classify("/data/media/movies/x.mkv") == "library" and d.classify("/data/tv/Show/x.mkv") == "library")
check("classify: unknown", d.classify("/data/stuff/x.mkv") == "other")
bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
