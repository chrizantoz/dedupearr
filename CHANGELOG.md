# Changelog

All dates 2026. Image: `ghcr.io/chrizantoz/dedupearr`.

## Unreleased — phase 2, ask the apps (2026-11-12)

- Download-client and arr readers: Deluge, NZBGet and qBittorrent (seeding state, ratio, time, label, per file) and the arrs' import history (which download became which library file). A download now becomes **Leftover from an upgrade**, **Orphan** or **Seeding only**, each row shows the client's state, and a **can go now** chip appears when your seeding rule (`SEED_MIN_RATIO` or `SEED_MIN_DAYS`) is met or nothing holds the file. Still read-only; the fixes are phase 3.
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
