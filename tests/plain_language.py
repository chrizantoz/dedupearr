"""Every sentence a person reads is plain: no raw codes, no paths in the sentence itself, ends with a full stop, says what happens next.
Run: python tests/plain_language.py   Exit 1 on failure."""
import os, re, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
os.environ.update(CONFIG_DIR=tempfile.mkdtemp(prefix="dedupearr-pl-"), SCAN_ROOTS="/nonexistent")
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
check("sizes read like a person says them", d.human(0) == "0 B" and d.human(999) == "999 B" and d.human(48_200_000_000) == "48.2 GB" and d.human(1_000_000_000_000) == "1 TB", [d.human(x) for x in (0, 999, 48_200_000_000, 1_000_000_000_000)])
check("friendly title: scene name", d.friendly_title("/x/Dune.Part.Two.2024.2160p.UHD.BluRay.x265-GRP.mkv") == "Dune Part Two 2024")
check("friendly title: keeps the episode number", d.friendly_title("/x/Shogun.S01E05.1080p.WEB.h264-GRP.mkv") == "Shogun S01E05")
check("friendly title: plain names untouched", d.friendly_title("/x/Heat (1995)/Heat 1995.mkv") == "Heat 1995")
check("classify: recycle wins over library", d.classify("/data/media/.trash/tv/x.mkv") == "recycle")
check("classify: downloads", d.classify("/data/torrents/movies/x.mkv") == "download" and d.classify("/mnt/user/downloads/complete/x.mkv") == "download")
check("classify: library", d.classify("/data/media/movies/x.mkv") == "library" and d.classify("/data/tv/Show/x.mkv") == "library")
check("classify: unknown", d.classify("/data/stuff/x.mkv") == "other")
bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
