# Dedupearr roadmap

Markers: 🟢 done · 🟡 in progress · 🔴 not started · 🙋 owner step. One line per item; what is checked is done and tested.

Proposal and research: the owner's doc "Dedupearr: the truth about your storage"; screen mockups on the "Dedupearr Mockups" canvas. Auditarr's own roadmap lists this app as N1–N4.

## Phase 1 · Scan and tell (read-only) — 2026-11-11

- 🟢 **P1.1** Disk scanner: every video file under `SCAN_ROOTS` with inode, device, link count, size, mtime; folder kind from explicit dirs or name hints.
- 🟢 **P1.2** Verdicts: Real copy, Only copy, Two names one file, Seeding name, Stored twice (sample + full checksum within a budget, cached per inode), Looks alike, Download without a match, Old / recent recycle-bin file, Unsorted folder. One plain sentence and one line of evidence each.
- 🟢 **P1.3** Page: Files (lead sentence, tiles, filters, search, evidence toggle, Ignore), Space (disk bar, verdict totals), About (health, settings, scans, log). Light and dark.
- 🟢 **P1.4** Nightly scan at `SCAN_TIME`, a scan at start when the last one is old, Scan now button, `/health` 503 when stale.
- 🟢 **P1.5** Tests: fake disk with every situation, verdict tests, plain-language tests, API contract test that also records the UI fixtures, jsdom UI bot, data fuzzer, button lint, CI with image build and Trivy.
- 🟡 **P1.6** First real-library run on the owner's server (read-only), compare the verdicts with reality for two weeks. Gate to phase 2.
- 🔴 **P1.7** Settings page (edit `RECYCLE_DAYS`, hints and dirs from the page); until then they are environment variables.
- 🙋 **P1.8** Make the GitHub repo public when happy; the image then pulls without a token.

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
