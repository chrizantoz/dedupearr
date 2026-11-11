# Dedupearr roadmap

Markers: 🟢 done · 🟡 in progress · 🔴 not started · 🙋 owner step. One line per item; what is checked is done and tested.

Proposal and research: the owner's doc "Dedupearr: the truth about your storage"; screen mockups on the "Dedupearr Mockups" canvas. Auditarr's own roadmap lists this app as N1–N4.

## Phase 1 · Scan and tell (read-only) — 2026-11-11

- 🟢 **P1.1** Disk scanner: every video file under `SCAN_ROOTS` with inode, device, link count, size, mtime; folder kind from explicit dirs or name hints.
- 🟢 **P1.2** Verdicts: Real copy, Only copy, Two names one file, Seeding name, Stored twice (sample + full checksum within a budget, cached per inode), Looks alike, Download without a match, Old / recent recycle-bin file, Unsorted folder. One plain sentence and one line of evidence each.
- 🟢 **P1.3** Page: Files (lead sentence, tiles, filters, search, evidence toggle, Ignore), Space (disk bar, verdict totals), About (health, settings, scans, log). Light and dark.
- 🟢 **P1.4** Nightly scan at `SCAN_TIME`, a scan at start when the last one is old, Scan now button, `/health` 503 when stale.
- 🟢 **P1.5** Tests, level with Auditarr (see docs/TESTING.md): ruff + pyflakes, fake disk with every situation, verdict tests, plain-language tests, API contract test that records the UI fixtures, random-disk fuzzer with invariants, chaos (unlistable/unreadable/missing/broken), mutation check (8 mutants, all caught), performance budget (60k files), jsdom UI bot, data fuzzer, five edge worlds, bot lints, real-browser bot with personas and screenshots, Trivy; errors tracked for the About page.
- 🟡 **P1.6** First real-library run on the owner's server (read-only) 2026-11-11: 15,114 files, 37.6 TB, 43 s; 476 hardlinked pairs, 1 identical copy (12.5 GB), 73 pairs identical except the first megabyte (Auditarr's in-place language tags; became the "Stored twice, header differs" verdict), 77 unmatched downloads (162 GB). Compare the verdicts with reality for two weeks. Gate to phase 2.
- 🔴 **P1.9** Phase 3 rule learned from P1.6: never relink a header-differs pair (the torrent client would put the old header back over the tagged library file); the fix there is "remove the download when seeding is done".
- 🔴 **P1.7** Settings page (edit `RECYCLE_DAYS`, hints and dirs from the page); until then they are environment variables.
- 🟢 **P1.8** Repo made public 2026-11-11; the owner flipped the GHCR package to public the same evening. The server now runs `ghcr.io/chrizantoz/dedupearr:latest` and the Unraid template points at it (the local build was removed).
- 🟢 **P1.10** Deployed on the owner's server 2026-11-11 as container `dedupearr` (:3092, `/mnt/user/media` read-only at `/data`, config in appdata), Unraid template installed; first container scan matched the host run (15,114 files). Browser bot run against the live page.

## Phase 2 · Ask the apps

- 🔴 **P2.1** qBittorrent reader (torrent list, content paths, seeding state, ratio, tracker) → an unmatched download becomes "still seeding", "leftover" (library has a newer file for the same title) or "orphan" (no torrent, no arr record).
- 🔴 **P2.2** Sonarr and Radarr readers: import history (copy vs hardlink), upgrades, current file per episode/movie; the "cause" column (e.g. "Radarr copied instead of linking: separate mounts").
- 🔴 **P2.3** Deluge, rTorrent, Transmission readers; recorded real API shapes in a fake stack, like Auditarr's.
- 🔴 **P2.4** Living next to Auditarr: read Auditarr's actions log; files Auditarr remuxed or rewrote get "rewritten by Auditarr, torrent still needed for seeding" and never a relink offer; Auditarr's `.trash` is a recycle bin.
- 🔴 **P2.5** Plex reader: which library files Plex actually indexes, so a "library" folder Plex ignores is flagged.
- 🔴 **P2.6** Phone digest (ntfy): space to get back, what needs you.

## Phase 3 · Fix safely

- 🔴 **P3.1** Dry run on by default with a banner; every fix shows the exact steps and the space it frees before it runs.
- 🔴 **P3.2** Relink (move the copy to the holding bin, hardlink the library file at the torrent path, verify link count and seeding, roll back on failure).
- 🔴 **P3.3** Remove old torrent + file through the client API; empty old recycle-bin files; move orphans to the holding bin.
- 🔴 **P3.4** Holding bin with 14-day undo; actions log; "fix all with this cause".
- 🔴 **P3.5** Per-file lock marker shared with Auditarr so the two never fix one file at once; night runs an hour apart.
- 🔴 **P3.6** Gate to phase 4: 30 nightly runs with zero wrong fixes.

## Phase 4 · Easy to install

- 🔴 **P4.1** Setup wizard: client and arr addresses, the shared-mount test ("your library and downloads are two mounts; here is the one-line change").
- 🔴 **P4.2** Unraid cache/array awareness (optional read-only `/mnt/disk*` mounts) so cross-disk cases get "move then link" or no fix.
- 🔴 **P4.3** Unraid Apps template, public demo in sandbox mode with the fake disk, hourly reset.
- 🔴 **P4.4** Soak gate and staged promotion like Auditarr's.
