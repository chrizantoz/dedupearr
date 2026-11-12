"""A fake disk with every situation Dedupearr judges, built in a temp folder. Used by the tests and by the sandbox.

build(root) -> dict name -> path. Files are MIN_SIZE_MB-sized blobs (the tests set MIN_SIZE_MB=1 so this stays fast)."""
import os, time

MB = 1024 * 1024

def blob(path, mb, seed, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    chunk = (seed.encode() * (MB // len(seed) + 1))[:MB]
    with open(path, "wb") as f:
        for _ in range(mb): f.write(chunk)
    if mtime is not None: os.utime(path, (mtime, mtime))
    return path

def paths(root):
    """The same dict build() returns, for a disk that already exists (the demo builds the disk first, then starts the fake clients)."""
    return build(root, rebuild=False)

def build(root, now=None, rebuild=True):
    now = now or time.time(); p = {}
    if not rebuild:
        global blob
        real_blob = blob
        def blob(path, mb, seed, mtime=None): return path   # only compute the paths
        try: return build(root, now, True)
        finally: blob = real_blob
    # a real copy: library file hardlinked into the torrent folder
    p["real_lib"] = blob(f"{root}/media/movies/Oppenheimer (2023)/Oppenheimer.2023.2160p.BluRay.x265.mkv", 3, "oppenheimer")
    p["real_seed"] = f"{root}/torrents/movies/Oppenheimer.2023.2160p.BluRay.x265/Oppenheimer.2023.2160p.BluRay.x265.mkv"
    if rebuild: os.makedirs(os.path.dirname(p["real_seed"]), exist_ok=True); os.link(p["real_lib"], p["real_seed"])
    # stored twice: identical content, two inodes
    p["dup_lib"] = blob(f"{root}/media/movies/Dune Part Two (2024)/Dune.Part.Two.2024.2160p.WEB-DL.mkv", 4, "dune2")
    p["dup_dl"] = blob(f"{root}/torrents/movies/Dune.Part.Two.2024.2160p.WEB-DL.mkv", 4, "dune2")
    # stored twice, but the library copy had its header edited in place (a language tag): same size, first bytes differ
    p["hdr_lib"] = blob(f"{root}/media/anime/Frieren/Season 01/Frieren.S01E01.1080p.BluRay.mkv", 4, "frieren1")
    p["hdr_dl"] = blob(f"{root}/torrents/anime/Frieren.S01E01.1080p.BluRay.mkv", 4, "frieren1")
    if rebuild:
        with open(p["hdr_lib"], "r+b") as f: f.seek(100); f.write(b"jpn-tagged-by-auditarr")
    # a download nothing matches
    p["unmatched"] = blob(f"{root}/torrents/tv/Shogun.S01E05.1080p.WEB.h264-GRP.mkv", 5, "shogun-old")   # a size no library file has
    # a different release of a movie the library already has (an upgrade left the old torrent behind)
    p["other_lib"] = blob(f"{root}/media/movies/Scary Movie (2026) {{tmdb-1273221}}/Scary Movie (2026) {{tmdb-1273221}} - {{edition-Theatrical Cut}} [Bluray-1080p][EAC3 7.1][x264]-playHD.mkv", 3, "scary-playhd")
    p["other_dl"] = blob(f"{root}/torrents/movies/Scary.Movie.2026.1080p.BluRay.x264-EniaHD.mkv", 6, "scary-eniahd")
    # same size as a library file, different content
    p["look_lib"] = blob(f"{root}/media/tv/Frieren/Season 01/Frieren.S01E12.1080p.mkv", 2, "frieren-good")
    p["look_dl"] = blob(f"{root}/torrents/tv/Frieren.S01E12.1080p.REPACK.mkv", 2, "frieren-other")
    # library only
    p["lib_only"] = blob(f"{root}/media/tv/The Bear/Season 03/The.Bear.S03E01.2160p.mkv", 2, "bear")
    # two names inside the library
    p["twin_a"] = blob(f"{root}/media/movies/Heat (1995)/Heat.1995.1080p.mkv", 2, "heat")
    p["twin_b"] = f"{root}/media/movies/Heat (1995)/Heat.1995.1080p.Directors.Cut.mkv"
    if rebuild: os.link(p["twin_a"], p["twin_b"])
    # recycle bin: old and recent
    p["bin_old"] = blob(f"{root}/media/.trash/tv/Fallout.S01E03.720p.mkv", 2, "fallout", mtime=now - 61 * 86400)
    p["bin_new"] = blob(f"{root}/media/.trash/tv/Fallout.S01E04.720p.mkv", 2, "fallout4", mtime=now - 2 * 86400)
    # unsorted folder
    p["other"] = blob(f"{root}/stuff/Home.Video.2019.mp4", 2, "home")
    # too small to count, and not a video
    p["small"] = blob(f"{root}/torrents/tv/sample.mkv", 0, "x")
    if rebuild: open(p["small"], "wb").write(b"tiny")
    p["text"] = blob(f"{root}/torrents/tv/notes.txt", 1, "notes")
    return p

if __name__ == "__main__":
    import sys
    print(build(sys.argv[1] if len(sys.argv) > 1 else "/tmp/dedupearr-fake"))
