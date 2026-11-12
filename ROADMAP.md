# Dedupearr roadmap

Markers: 🟢 done · 🟡 in progress · 🔴 not started · 🙋 owner step. One line per item; what is checked is done and tested.

Proposal and research: the owner's doc "Dedupearr: the truth about your storage"; screen mockups on the "Dedupearr Mockups" canvas. Auditarr's own roadmap lists this app as N1–N4.

## 🗂️ How we track work (added 2026-11-12)

- **This file** is the big picture: phases, what is done, what is next, in plain words.
- **The board** across both apps: https://github.com/users/chrizantoz/projects/1 (cards grouped by status, app, area or phase).
- **GitHub Issues** is the inbox and the day-to-day list. Anything found along the way becomes one issue with one label: `bug`, `bot` (the test bots), `feature`, `ux` (friendlier wording, navigation, flow), `design` (look and feel), `idea`, `owner-step`. Milestones match the phases here.
- **Rule:** an idea is written down the moment it comes up, as an issue, not remembered. When an issue ships, this file gets its line and the issue closes.

## Phase 1 · Scan and tell (read-only) — 2026-11-11

- 🟢 **P1.1** Disk scanner: every video file under `SCAN_ROOTS` with inode, device, link count, size, mtime; folder kind from explicit dirs or name hints.
- 🟢 **P1.2** Verdicts: Real copy, Only copy, Two names one file, Seeding name, Stored twice (sample + full checksum within a budget, cached per inode), Looks alike, Download without a match, Old / recent recycle-bin file, Unsorted folder. One plain sentence and one line of evidence each.
- 🟢 **P1.3** Page: Files (lead sentence, tiles, filters, search, evidence toggle, Ignore), Space (disk bar, verdict totals), About (health, settings, scans, log). Light and dark.
- 🟢 **P1.4** Nightly scan at `SCAN_TIME`, a scan at start when the last one is old, Scan now button, `/health` 503 when stale.
- 🟢 **P1.5** Tests, level with Auditarr (see docs/TESTING.md): ruff + pyflakes, fake disk with every situation, verdict tests, plain-language tests, API contract test that records the UI fixtures, random-disk fuzzer with invariants, chaos (unlistable/unreadable/missing/broken), mutation check (8 mutants, all caught), performance budget (60k files), jsdom UI bot, data fuzzer, five edge worlds, bot lints, real-browser bot with personas and screenshots, Trivy; errors tracked for the About page.
- 🟢 **P1.6** First real-library run on the owner's server (read-only) 2026-11-11: 15,114 files, 37.6 TB, 43 s; 476 hardlinked pairs, 1 identical copy (12.5 GB), 73 pairs identical except the first megabyte (Auditarr's in-place language tags; became the "Stored twice, header differs" verdict), 77 unmatched downloads (162 GB). Compare the verdicts with reality for two weeks. Gate to phase 2. Done; the nightly scan has run clean since.
- 🔴 **P1.9** Phase 3 rule learned from P1.6: never relink a header-differs pair (the torrent client would put the old header back over the tagged library file); the fix there is "remove the download when seeding is done".
- 🟢 **P1.11** (2026-11-12) "Different release in library": an unmatched download whose title and year (movies) or title and episode (shows) match a library file is called a likely leftover and points at the keeper. Found 11 of 77 on the owner's server on the first pass (Scary Movie, Bleach, Mushoku Tensei, Slime, One Piece).
- 🔴 **P1.12** Title matching cannot bridge a Japanese torrent name and an English library name (Isekai Maou → How Not to Summon a Demon Lord), absolute episode numbers (One Piece EP1177) or fansub names ([MTBB] Mushoku Tensei S3 - 01): those need Sonarr's own mapping, which is P2.2. Extras (NCOP/NCED), ISOs and software in the download folders are not library material and should get their own quiet verdict instead of "to decide".
- 🔴 **P1.7** Settings page (edit `RECYCLE_DAYS`, hints and dirs from the page); until then they are environment variables.
- 🟢 **P1.8** Repo made public 2026-11-11; the owner flipped the GHCR package to public the same evening. The server now runs `ghcr.io/chrizantoz/dedupearr:latest` and the Unraid template points at it (the local build was removed).
- 🟢 **P1.10** Deployed on the owner's server 2026-11-11 as container `dedupearr` (:3092, `/mnt/user/media` read-only at `/data`, config in appdata), Unraid template installed; first container scan matched the host run (15,114 files). Browser bot run against the live page.

## Phase 2 · Ask the apps

Why this phase matters on the owner's server (2026-11-12): 357 GB of header-differs copies and most of the 162 GB of unmatched downloads have one fix, "remove the download once seeding is done", and that needs the torrent client's answer. P2.1 is the next piece of work.

- 🟡 **P2.0** (readers 2026-11-12; discovery from the arrs' settings pages still to come) Nothing about a user's setup is hard-coded: Dedupearr asks Sonarr and Radarr which download clients they use (`/api/v3/downloadclient`: kind, address, category, enabled, priority) and reads every enabled one; a torrent-first household and a usenet-first household get the same verdicts from the same code. Clients the arrs do not know (a standalone seedbox) can be added on the Setup page.
- 🟢 **P2.1** (2026-11-12: Deluge, NZBGet, qBittorrent, with a fake stack in `tests/mock_clients.py` and `tests/clients.py`; the seeding rule `SEED_MIN_RATIO` / `SEED_MIN_DAYS` / `SEED_RULE` decides "can go now"; owner's rule 2026-11-12: ratio 1.0 and 14 days, which is now the default) Download-client readers, in the order the owner's arrs actually use them (checked 2026-11-12: Sonarr, Radarr and Whisparr all send to **NZBGet** (usenet, primary) and **Deluge** (torrents, inside the VPN container); rTorrent and Transmission are configured but disabled): **NZBGet** first (JSON-RPC: history and its import status, so a finished usenet download still on disk is a leftover or a failed import, never "seeding"), then **Deluge** (web JSON API: torrent list, content paths, seeding state, ratio, tracker), then **qBittorrent** for everyone else. An unmatched download becomes "still seeding", "leftover" (the library has a newer file for the same title) or "orphan" (no client knows it, no arr record).
- 🟡 **P2.2** Sonarr, Radarr and Whisparr import history read 2026-11-12 (download path → library file → "Leftover" when that file is gone). Still to come: the "cause" column (copy vs hardlink from the arr's history), and the arr's own title mapping for fansub and absolute-numbered names (P1.12).
- 🟢 **P2.9** (2026-11-12) Bot gate for phase 2: fake client stack shared by tests, fixtures and the public demo; client-answer fuzzer; page bot checks the client line and the "can go now" chips against the data; browser-bot persona "what can I free right now?". Rule from here on: no phase starts until the bots cover the one before it (docs/TESTING.md, "The gate before each phase").
- 🔴 **P2.3** rTorrent (XML-RPC) and Transmission readers; recorded real API shapes for every client in a fake stack, like Auditarr's.
- 🟢 **P2.7** Kinds of media (owner request 2026-11-12): every file gets a kind (TV shows, Anime, Movies, Adult, Sports, Music, Other) from the arrs' root folders and Sonarr's anime flag first, then the linked library file, the importing arr, the client label and folder names; a kind row under the filters, a tag per row, a per-kind table on Space. Lidarr now read at its v1 API.
- 🔴 **P2.4** Living next to Auditarr: read Auditarr's actions log; files Auditarr remuxed or rewrote get "rewritten by Auditarr, torrent still needed for seeding" and never a relink offer; Auditarr's `.trash` is a recycle bin.
- 🔴 **P2.5** Plex reader: which library files Plex actually indexes, so a "library" folder Plex ignores is flagged.
- 🔴 **P2.6** Phone digest (ntfy): space to get back, what needs you.

## Phase 3 · Fix safely

- 🟡 **P3.0** (2026-11-12: `tests/fixes.py` proves a dry run leaves disk and clients untouched, every fixable row has a plan in sentences, a fix never runs during a scan, every attempt is logged; the fake clients record remove calls; both bots press Preview fix. Still to come: the nightly bot against production with the drift guard and the staged soak.) Bot gate before the first fix: the fake clients accept and record remove calls (already in the mock), a dry run must leave disk and clients untouched, every fix has an undo test, fix-during-scan guard, nightly bot against production with the drift guard, staged soak before promotion (Auditarr's deploy script pattern).

- 🟡 **P3.1** (2026-11-12: dry run on by default with the banner, `FIX_MODE`, plans per verdict, `Preview fix`, actions log. Still to come: the holding bin moves, the 14-day undo and the restore button.) Dry run on by default with a banner; every fix shows the exact steps and the space it frees before it runs.
- 🔴 **P3.2** Relink (move the copy to the holding bin, hardlink the library file at the torrent path, verify link count and seeding, roll back on failure).
- 🔴 **P3.3** Remove old torrent + file through the client API; empty old recycle-bin files; move orphans to the holding bin.
- 🔴 **P3.4** Holding bin with 14-day undo; actions log; "fix all with this cause".
- 🔴 **P3.5** Per-file lock marker shared with Auditarr so the two never fix one file at once; night runs an hour apart.
- 🔴 **P3.6** Gate to phase 4: 30 nightly runs with zero wrong fixes.

## Phase 4 · Easy to install

- 🔴 **P4.1** Setup wizard: client and arr addresses, the shared-mount test ("your library and downloads are two mounts; here is the one-line change").
- 🔴 **P4.2** Unraid cache/array awareness (optional read-only `/mnt/disk*` mounts) so cross-disk cases get "move then link" or no fix.
- 🟢 **P4.3** Unraid Apps template (template file exists; Community Apps submission is the owner's step). 🟢 Public demo in sandbox mode with the fake disk and hourly reset: https://dedupearr-demo.chrizantoz.com (2026-11-12); project site with a static copy of the page on GitHub Pages. (2026-11-12: template in chrizantoz/unraid-templates; Community Apps confirmed the repository is already enabled, Dedupearr appears on the next feed build.)
- 🟢 **P4.4** Soak gate and staged promotion like Auditarr's. (2026-11-12: dedupearr-deploy.sh stage/update on the owner's server: staging copy on a database copy, verdict comparison, page bot, promote on PASS; first live run caught the restart bug, #17.)
