# Dedupearr roadmap

Markers: 🟢 done · 🟡 in progress · 🔴 not started · 🙋 owner step. One line per item; what is checked is done and tested.

Proposal and research: the owner's doc "Dedupearr: the truth about your storage"; screen mockups on the "Dedupearr Mockups" canvas. Auditarr's own roadmap lists this app as N1–N4.

## 🗂️ How we track work (added 2026-11-12)

- **This file** is the big picture: phases, what is done, what is next, in plain words.
- **The board** across both apps: https://github.com/users/chrizantoz/projects/1 (cards grouped by status, app, area or phase).
- **GitHub Issues** is the inbox and the day-to-day list. Anything found along the way becomes one issue with one label: `bug`, `bot` (the test bots), `feature`, `ux` (friendlier wording, navigation, flow), `design` (look and feel), `idea`, `owner-step`. Milestones match the phases here.
- **Rule:** an idea is written down the moment it comes up, as an issue, not remembered. When an issue ships, this file gets its line and the issue closes.
- **GitHub's own bots count too (added 2026-11-12).** Code scanning (CodeQL), Dependabot and secret scanning alerts become issues labelled `security`, opened by the `alerts` workflow every morning (one issue per alert, never twice). Each one is fixed with a test, or dismissed on GitHub with a written reason; never left open without a look.
- **Every bug teaches something.** When a bug is fixed, its lesson goes under *What bugs taught us* below: the pattern behind it, the guard that now stops it (a test or a bot world), and any idea it gave for later (filed as an `idea` issue).

## 🛡️ Security alerts

- 🟢 (2026-11-12) CodeQL #11/#12: the container-settings password was compared through a quick SHA-256; it is now slow-hashed (PBKDF2, 200k rounds) like a page-set one. Gate: `tests/auth.py`.
- 🟢 (2026-11-12) CodeQL #3–#8: the holding bin now looks up the scan's record of a file before it touches the disk, and works only from the recorded path. The old checks (inside the scan folders, same file as scanned) already stopped misuse; this makes the order plain. Gate: `tests/fixes.py`.

## 📚 What bugs taught us (added 2026-11-12)

Each line: the pattern → the guard that stops it now → the idea it left for later.

- **A fix acts on what is true now, not on last night's scan** (P3.2, 2026-11-14: the scan can be a day old when the button is pressed). → The remove fix asks the client again first and refuses when the torrent is no longer past the rule or holds other large files; mutants prove both guards. → 💡 Every later fix (relink, empty the bin) re-checks its facts the same way.
- **Undo must say what it cannot bring back** (P3.2: Put back returns the file, not the torrent). → The button hint, the result and the holding-bin row all say so. → 💡 Keep the torrent's magnet link with the held file, so Put back can offer to add it again.
- **A missing button must say why** (#23: 110 rows had no Preview fix, correctly - still seeding - but nothing said so). → The row says when it can go, where the button would be; a page-bot check. → 💡 The same in Auditarr: every row whose fix is held back says by what.
- **One rule for every torrent is wrong for most of them** (#23: ratio 1 *and* 14 days kept public torrents forever). → `SEED_RULE=auto` follows each torrent's private flag. → 💡 Show on About how many torrents are private and public.
- **Usenet is not torrents** (#18 by a contributor: 243 GB of a failed NZBGet job hidden as "Seeding only"). → Finished or failed usenet jobs nobody imported are *Download without a match*; a test and a mutant guard it. → #20 done 2026-11-12: *Still downloading* and *Waiting for import* (with the arr's reason when it is stuck).
- **What the clients said must survive a restart** (#17). → Stored and reloaded at start. → 💡 The page bot restarts the app mid-session and expects the same answers.
- **A test that runs one case a day can break quietly** (2026-11-12: a security fix moved the line one mutant looks for; the daily rotation would not have said so for weeks). → `mutation_check.py anchors` checks every mutant's line on every CI run.
- **A security scanner cannot see guards that are spread out** (CodeQL #3–#8). → Look up first, act on the looked-up value. → 💡 Write file actions in that order from the start.

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
- 🟢 **P2.10** (2026-11-13) Seeding rule per torrent: `auto` (default) = ratio and days for private torrents, either for public ones, from the client's private flag; a row still seeding says when it can go (#23).
- 🟢 **P2.9** (2026-11-12) Bot gate for phase 2: fake client stack shared by tests, fixtures and the public demo; client-answer fuzzer; page bot checks the client line and the "can go now" chips against the data; browser-bot persona "what can I free right now?". Rule from here on: no phase starts until the bots cover the one before it (docs/TESTING.md, "The gate before each phase").
- 🔴 **P2.3** rTorrent (XML-RPC) and Transmission readers; recorded real API shapes for every client in a fake stack, like Auditarr's.
- 🟢 **P2.7** Kinds of media (owner request 2026-11-12): every file gets a kind (TV shows, Anime, Movies, Adult, Sports, Music, Other) from the arrs' root folders and Sonarr's anime flag first, then the linked library file, the importing arr, the client label and folder names; a kind row under the filters, a tag per row, a per-kind table on Space. Lidarr now read at its v1 API.
- 🔴 **P2.4** Living next to Auditarr: read Auditarr's actions log; files Auditarr remuxed or rewrote get "rewritten by Auditarr, torrent still needed for seeding" and never a relink offer; Auditarr's `.trash` is a recycle bin.
- 🔴 **P2.5** Plex reader: which library files Plex actually indexes, so a "library" folder Plex ignores is flagged.
- 🔴 **P2.6** Phone digest (ntfy): space to get back, what needs you.

## Phase 3 · Fix safely

- 🟢 **P3.0** (2026-11-14: the gate is complete. The nightly page bot runs against production and reports a build mismatch; the server's update script soaks every new build on a copy of the database, compares all verdicts and runs the page bot before it promotes. Earlier, 2026-11-12: `tests/fixes.py` proves a dry run leaves disk and clients untouched, every fixable row has a plan in sentences, a fix never runs during a scan, every attempt is logged; the fake clients record remove calls; both bots press Preview fix. ) Bot gate before the first fix: the fake clients accept and record remove calls (already in the mock), a dry run must leave disk and clients untouched, every fix has an undo test, fix-during-scan guard, nightly bot against production with the drift guard, staged soak before promotion (Auditarr's deploy script pattern).

- 🟢 **P3.1** (2026-11-12: dry run on by default with the banner, `FIX_MODE`, plans per verdict, `Preview fix`. Holding bin: a rename into `HOLDING_DIR/<day>/<old path>` after re-checking inode and size, Put back that refuses an occupied place, purge after `HOLD_DAYS` that only deletes the file that went in, Actions tab with the bin and the log, two-press confirm. The first live fix is orphans → holding bin; every other fix stays a dry run. Gate: `tests/fixes.py` (29 checks), three holding-bin mutants, page-bot world `live`.) Dry run on by default with a banner; every fix shows the exact steps and the space it frees before it runs.
- 🟢 **Sign-in** (2026-11-12, ahead of the first live fixes): Protect the page on About (hashed password; the first one only from your own network) or `AUTH_USER`/`AUTH_PASSWORD`, signed cookie, Sign out; "No sign-in on my own network" (or `LOCAL_NO_LOGIN=1`) skips it for a direct request from your own network, never through a proxy; `TRUST_PROXY_AUTH=1`. Gate: `tests/auth.py` (51 checks), page bot checks Sign out, the password form and the home-network box per world.
- 🟢 **P3.2** (2026-11-14, #7) Remove a leftover or second copy through the client, once its seeding is done. The client is asked again right before the change (last night's scan is not trusted); the file goes to the holding bin first; then Deluge or qBittorrent is told to forget the torrent and keep its files; if the client refuses, the file goes straight back. A torrent that holds other large files is left alone. Usenet leftovers and files no client knows go to the holding bin with no client call. Gate: `tests/fixes.py` (43 checks), four `remove fix` mutants, page-bot world `live`.
- 🔴 **P3.3** Relink (move the copy to the holding bin, hardlink the library file at the torrent path, verify link count and seeding, roll back on failure).
- 🟡 **P3.4** Orphans to the holding bin (done with P3.1); empty old recycle-bin files (to do).
- 🔴 **P3.5** "Fix all with this cause", including a whole multi-file torrent at once; per-file lock marker shared with Auditarr so the two never fix one file at once; night runs an hour apart.
- 🔴 **P3.6** Gate to phase 4: 30 nightly runs with zero wrong fixes.

## Phase 4 · Easy to install

- 🔴 **P4.1** Setup wizard: client and arr addresses, the shared-mount test ("your library and downloads are two mounts; here is the one-line change").
- 🔴 **P4.2** Unraid cache/array awareness (optional read-only `/mnt/disk*` mounts) so cross-disk cases get "move then link" or no fix.
- 🟢 **P4.3** Unraid Apps template (template file exists; Community Apps submission is the owner's step). 🟢 Public demo in sandbox mode with the fake disk and hourly reset: https://dedupearr-demo.chrizantoz.com (2026-11-12); project site with a static copy of the page on GitHub Pages. (2026-11-12: template in chrizantoz/unraid-templates; Community Apps confirmed the repository is already enabled, Dedupearr appears on the next feed build.)
- 🟢 **P4.4** Soak gate and staged promotion like Auditarr's. (2026-11-12: dedupearr-deploy.sh stage/update on the owner's server: staging copy on a database copy, verdict comparison, page bot, promote on PASS; first live run caught the restart bug, #17.)
