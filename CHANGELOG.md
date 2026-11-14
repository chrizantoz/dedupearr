# Changelog

All dates 2026. Image: `ghcr.io/chrizantoz/dedupearr`.

## Unreleased — phase 2, ask the apps; phase 3 begins (2026-11-12)

- New: **Move all shown to the holding bin**. With fixes switched on and two or more plain moves on the page, one button above the list moves them all, one after the other, each through the same checks as its own button. It follows the filter, the kind and the search box, asks to be sure first, and names any file it left alone. A row that also removes a torrent never rides along, because that cannot be undone; it stays one press on its own row. First part of P3.5 (#10).
- Fix: after a real fix, the page showed "Failed to fetch" when its own refresh failed, although the file had moved. A done fix now says it is done and asks you to reload; a fix that stops with an error answers in words and is kept under Recent errors; every real fix is written to the log (#25).
- New, the first fix that talks to a download client (P3.2, #7): with fixes switched on, a leftover or second copy whose seeding is done gets **Remove torrent, move to holding bin**. Dedupearr asks the client again right before the change, moves the file to the holding bin, then tells Deluge or qBittorrent to forget the torrent and keep its files. If the client refuses, the file goes straight back. A torrent that holds other large files is left alone. **Put back** returns the file for 14 days, not the torrent. Usenet leftovers and files no client knows just move to the holding bin. Nothing changes while `FIX_MODE` is `dryrun` (the default).
- New: a leftover or second copy that is still seeding says where the button would be when it can go ("Still seeding · can go in about 4 days, or sooner at ratio 1"), instead of just having no **Preview fix**. The seeding rule is now `auto` by default: private torrents need the ratio and the days, public ones either (the client's private flag decides) (#23).
- New verdicts **Still downloading** (a client is still downloading it; no more "Seeding only" and torrent words for a usenet job) and **Waiting for import** (finished, and an arr's queue is importing it, or cannot import it on its own; the arr's reason is shown, with where to fix it). Raised by @jonneyblaze in #18 (#20).
- Security: the password set in the container settings is slow-hashed like a page-set one; the holding bin looks up the scan's record before touching the disk (CodeQL, #21). New `alerts` workflow turns each code-scanning alert into an issue every morning.
- Phase 3 starts as a dry run: every fixable row has a **Preview fix** button that shows the exact steps (relink, remove through the client, holding bin, empty an old bin) and the space it would free; `FIX_MODE=dryrun` is the default and nothing is moved, linked or deleted. Every preview is written to the actions log (`/api/actions`). A fix is refused while a scan runs. The real fixes arrive one at a time behind that switch, each with a holding bin and a 14-day undo.

- Download-client and arr readers: Deluge, NZBGet and qBittorrent (seeding state, ratio, time, label, per file) and the arrs' import history (which download became which library file). A download now becomes **Leftover from an upgrade**, **Orphan** or **Seeding only**, each row shows the client's state, and a **can go now** chip appears when your seeding rule (`SEED_MIN_RATIO` and `SEED_MIN_DAYS`, both by default; `SEED_RULE=either` relaxes it) is met or nothing holds the file. Still read-only; the fixes are phase 3.
- Fix: a usenet download that NZBGet finished or failed and no arr imported was called **Seeding only** and counted as 0 B, so a failed job's partial files could hide hundreds of gigabytes (243 GB on one real server). Usenet never seeds: such a file is now a **Download without a match**, counted in full, and its sentence says what NZBGet did with the job. Checks in `tests/clients.py` and `tests/plain_language.py`, and a mutant.
- Sportarr and Lidarr are read like Sonarr and Radarr (`SPORTARR_URL` / `LIDARR_URL` and their keys).
- A client that fails to answer is reported on About and its files fall back to the content-only verdicts; nothing is guessed.
- Download-folder names now include `completed`, `intermediate`, `nzbget`, `sabnzbd` and friends, so usenet folders are not mistaken for the library.

## Phase 1 — scan and tell (2026-11-11)

- First version. Scans the library and download folders, groups names that share one inode, confirms identical copies with checksums, judges recycle bins, and explains every file in one plain sentence with its evidence.
- Files, Space and About pages; nightly scan; Scan now; Ignore.
- New verdict from the first real-library run: "Stored twice, header differs": same size, middle and end identical, only the first megabyte differs, which is a language tag or title edited in place on one copy (Auditarr does this). Relinking such a pair is not safe, and the sentence says so.
- New verdict "Different release in library": a download nothing matches byte for byte, while the library holds another release of the same title (matched on name and year, or name and episode). Almost always a leftover from an upgrade, and the row now says so and names the library file (the Scary Movie case, 2026-11-12).
- Public demo at https://dedupearr-demo.chrizantoz.com (`SANDBOX=1`: the fake disk, per-visitor rate limit, hourly reset) and a project site with a static copy of the page (GitHub Pages).
- Read-only: nothing is moved, linked or deleted.
- Tests: fake disk, verdicts, plain language, API contract, UI bot, data fuzzer, button lint; CI builds and scans the image.
