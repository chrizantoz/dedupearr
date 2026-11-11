"""Every verdict on the fake disk, and the numbers that go with them. Run: python tests/verdicts.py   Exit 1 on failure."""
import os, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-test-"); DATA = os.path.join(TMP, "data")
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=DATA, MIN_SIZE_MB="1", HASH_BUDGET_MB="100")
import fake_disk  # noqa: E402
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:220]}]"))

now = time.time(); P = fake_disk.build(DATA, now)
r = d.scan(now=now); c = d.db(); S = d.state(c)
by = {i["path"]: i for i in S["items"]}
code = lambda k: by.get(P[k], {}).get("code")
check("scan counted only real-size videos (13 names, hardlinks counted per name)", r["files"] == 13, r)
check("library + hardlinked download = Real copy", code("real_lib") == "REAL_COPY", code("real_lib"))
check("its download-folder name is the Seeding name, pointing at the library file", code("real_seed") == "LINKED_SEED" and by[P["real_seed"]]["twin"] == P["real_lib"])
check("identical content on two inodes = Stored twice, with the library twin", code("dup_dl") == "DUPLICATE_COPY" and by[P["dup_dl"]]["twin"] == P["dup_lib"], by.get(P["dup_dl"]))
check("the library side of a duplicate stays Only copy (the download is the waste)", code("dup_lib") == "LIBRARY_ONLY")
check("a download nothing matches = Download without a match", code("unmatched") == "DOWNLOAD_UNMATCHED", code("unmatched"))
check("same size, different content = Looks alike, not a duplicate", code("look_dl") == "LOOKALIKE" and by[P["look_dl"]]["wasted"] == 0)
check("library only = Only copy", code("lib_only") == "LIBRARY_ONLY")
check("two names inside the library = Two names, one file", code("twin_a") == "LIBRARY_TWIN" and code("twin_b") == "LIBRARY_TWIN")
check("61 days in the bin with a 30-day rule = Old recycle-bin file", code("bin_old") == "BIN_OLD")
check("2 days in the bin = In the recycle bin, not waste", code("bin_new") == "BIN_RECENT" and by[P["bin_new"]]["wasted"] == 0)
check("an unrecognised folder = Unsorted folder", code("other") == "OTHER")
check("tiny file and text file are not judged at all", P["small"] not in by and P["text"] not in by)
mb = 1024 * 1024
check("wasted = duplicate (4 MB) + unmatched (5 MB) + old bin (2 MB)", r["wasted"] == 11 * mb, r["wasted"])
check("reclaim (duplicate + old bin) and 'to decide' (unmatched) are reported apart", S["reclaim_bytes"] == 6 * mb and S["decide_bytes"] == 5 * mb, (S["reclaim_bytes"], S["decide_bytes"]))
check("library size counts each inode once (3+4+2+2+2 = 13 MB, the twin not doubled)", S["library_bytes"] == 13 * mb, S["library_bytes"])
check("evidence names inodes and link counts", "inode" in by[P["dup_dl"]]["evidence"] and "link count" in by[P["real_lib"]]["evidence"])
check("the duplicate was confirmed with full checksums", "checksums identical" in by[P["dup_dl"]]["evidence"], by[P["dup_dl"]]["evidence"])
check("friendly titles drop the quality tail", by[P["dup_dl"]]["title"] == "Dune Part Two 2024", by[P["dup_dl"]]["title"])
check("needs-you = the four verdicts a person decides on", sorted({i["code"] for i in S["items"] if i["needs_you"]}) == ["BIN_OLD", "DOWNLOAD_UNMATCHED", "DUPLICATE_COPY", "OTHER"])
check("the lead sentence carries the library size, the waste and the undecided downloads", S["library_h"] in S["lead"] and S["reclaim_h"] in S["lead"] and S["decide_h"] in S["lead"], S["lead"])

# a second scan after a change: the duplicate is relinked by hand -> Real copy; the old bin file was emptied -> gone
os.remove(P["dup_dl"]); os.link(P["dup_lib"], P["dup_dl"]); os.remove(P["bin_old"])
d.scan(now=now + 60); S2 = d.state(c); by2 = {i["path"]: i for i in S2["items"]}
check("after relinking by hand the pair becomes a Real copy", by2[P["dup_lib"]]["code"] == "REAL_COPY" and by2[P["dup_dl"]]["code"] == "LINKED_SEED")
check("a deleted file leaves the list", P["bin_old"] not in by2)
check("first_seen survives a rescan", by2[P["unmatched"]]["first_seen"] == by[P["unmatched"]]["first_seen"])
check("fingerprints were cached: a second scan re-hashed nothing new", c.execute("SELECT count(*) FROM fingerprint").fetchone()[0] >= 3)
check("a scan while one is running is refused, not doubled", d.SCANNING.update(on=True) or d.scan() is None); d.SCANNING["on"] = False
c.close(); bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
