# 💾 Dedupearr — what it does

Dedupearr is a nightly storage-truth bot for a **Sonarr / Radarr / torrent-client** library. Each night it walks every video file in the library and the download folder, works out what each file *is*, and explains it in one sentence with the evidence. Phase 1 is read-only.

### 📖 A few words you'll see

- **A "verdict"** is what Dedupearr decided one file is: a real copy, a second copy, a download without a match, …
- 🔗 **Hardlink**: two names for one file on disk. Your library file and the seeding torrent *should* be a hardlink, so the file is stored once. The evidence for it is a shared **inode** (the file's id on disk) and a **link count** of 2 or more.
- 📄 **Copy**: two files with the same content. Twice the space. Happens when the arr could not hardlink (two mounts) and copied instead.
- 🧾 **Evidence**: the numbers behind a verdict: inodes, link counts, sizes, and whether the checksums of the first, middle and last megabyte (or the whole file) matched.
- 🗑️ **Recycle bin**: the folder your arr or Auditarr moves replaced files into (`.recycle`, `.trash`, …). Files older than `RECYCLE_DAYS` count as waste.

## 🩺 The verdicts

The left column is the internal code (handy when searching the code or the API); the middle column is what it means.

| Code | In plain terms | Wasted |
|---|---|---|
| REAL_COPY | the library file and the download are one file with two names (hardlink); seeding costs nothing | 0 |
| LINKED_SEED | the download-folder name of a REAL_COPY | 0 |
| LIBRARY_ONLY | one copy, in the library | 0 |
| LIBRARY_TWIN | two names inside the library share one file | 0 |
| DUPLICATE_COPY | library file and download are two files with identical content (checksums match) | the file's size |
| DUPLICATE_HEADER | same, except the first megabyte: a tag or title was edited in place on one copy; never to be relinked | the file's size |
| LOOKALIKE | same size as a library file, different content | 0 |
| DOWNLOAD_UNMATCHED | no library file has the same content; phase 2 asks the client whether it is a leftover, an orphan or still seeding | the file's size, counted as "to decide" |
| DOWNLOAD_OTHER_RELEASE | no identical file, but the library holds a different release of the same title (matched on title and year, or title and episode): most likely a leftover from an upgrade | the file's size, counted as "to decide" |
| LEFTOVER | an arr's history says this download was imported, and the library file it became has since been replaced or rewritten | the file's size |
| ORPHAN | no download client knows the file and no arr imported it; nothing holds it | the file's size |
| SEEDING_ONLY | a client seeds it and no arr ever imported it (software, extras, torrents you keep on purpose) | 0 |
| BIN_OLD | in a recycle bin longer than `RECYCLE_DAYS` | the file's size |
| BIN_RECENT | in a recycle bin, within the rule | 0 |
| OTHER | the folder name gives no hint (not library, downloads or recycle bin); set `LIBRARY_DIRS` / `DOWNLOAD_DIRS` / `RECYCLE_DIRS` | 0 |

**Needs a decision** = DUPLICATE_COPY, DUPLICATE_HEADER, DOWNLOAD_UNMATCHED, DOWNLOAD_OTHER_RELEASE, LEFTOVER, ORPHAN, BIN_OLD and OTHER. Everything else is fine and shown under *Everything fine*. Each download row also carries its client's state ("Deluge: seeding, ratio 1.31, 3 days") and a **can go now** chip when your seeding rule (`SEED_MIN_RATIO` and `SEED_MIN_DAYS`, both reached by default; `SEED_RULE=either` relaxes it) is met or nothing holds the file.

## 📄 The pages

- **Files**: lead sentence (library size, waste, undecided downloads), five tiles that double as filters, filter chips, a row of kind buttons (TV shows, Anime, Movies, Adult, Sports, Music, Other) whose counts follow the filter, search, one row per file with its kind tag, chip, sentence, evidence, size, wasted space and an Ignore button.
- **Space**: one bar for the share (library · waste · to decide · other · free) a table of every verdict with count and space, and a table per kind of media (files, need a decision, wasted).
- **About**: health (last scan, errors in the last 24 hours, read-only reminder), settings in force, scan history with durations, recent errors, log.

## 🔘 The buttons

| Button | Where | What it does |
|---|---|---|
| Scan now | header | starts a scan; the header shows phase and file count while it runs |
| Ignore / Un-ignore | a row that needs a decision | hides the row from Needs a decision (it stays under Everything with an *ignored* chip) and brings it back |
| evidence | under every sentence | shows the inodes, link counts, sizes, checksum result and the other file's path |
| Preview fix | a fixable row | shows the exact steps (relink / remove through the client / holding bin / empty an old bin) and the space freed; a dry run by default (`FIX_MODE=dryrun`), logged in `/api/actions` |

Phase 3 adds: Relink, Remove old torrent, Empty bin, Move to holding bin, Undo, Apply to all with this cause.

## 🌙 What runs by itself

- A scan at `SCAN_TIME` every night, and at start when the last scan is more than 20 hours old.
- Checksums only for same-size library and download pairs, remembered per file, capped per scan by `HASH_BUDGET_MB`.
- `/health` answers 503 when the last scan failed or is more than two days old, for your uptime monitor.

## 🔌 API (for scripts and dashboards)

| Call | Answer |
|---|---|
| `GET /api/state` | everything the Files and Space pages show: lead, counts, wasted bytes per verdict, disks, items with verdict, sentence and evidence |
| `GET /api/about` | version, health lines, settings, scans, recent errors, log |
| `POST /api/scan` | starts a scan |
| `POST /api/ignore` `{"path": …}` / `POST /api/unignore` | hide or show a row |
| `POST /api/fix` `{"path": …}` | the fix plan for a row (dry run: steps and space freed; refused during a scan or when the seeding rule says wait) |
| `GET /api/actions` | the actions log |
| `GET /health` | 200 when the last scan is recent and succeeded, else 503 |
