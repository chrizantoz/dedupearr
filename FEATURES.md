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
| DOWNLOADING | a client is still downloading it (usenet or torrent); looked at again once it finishes | 0 |
| AWAITING_IMPORT | finished, and Sonarr/Radarr/… has it in its queue to import, or cannot import it on its own (the reason is shown, with where to fix it) | 0 |
| SEEDING_ONLY | a client seeds it and no arr ever imported it (software, extras, torrents you keep on purpose); the sentence says when the name marks it as an extra or a disc image | 0 |
| EXTRA | **Extra, not library**: a download whose name or folder marks it as not library material (an opening or ending without credits, NCOP/NCED; a trailer, sample, menu or making-of; a disc image, `.iso`; software or games), when no client or arr says more. Quiet: not waste and not a decision. An episode whose title merely says *Menu* or *Samples* stays a download to decide on; a finished usenet job stays a decision | 0 |
| BIN_OLD | in a recycle bin longer than `RECYCLE_DAYS` | the file's size |
| BIN_RECENT | in a recycle bin, within the rule | 0 |
| NOT_IN_PLEX_FOLDER | **Outside Plex's folders** (only with Plex connected): a library file in a folder no Plex library looks in, so Plex never shows it. Add the folder to a Plex library, move the file, or press Ignore if it is meant to stay out | 0 |
| NOT_IN_PLEX | **Plex does not show it** (only with Plex connected): a library file inside a Plex library folder that Plex has not indexed: a name Plex cannot match, a trailer or other extra, or a scan that has not run. Never given to a file less than a day old. A quiet note | 0 |
| OTHER | the folder name gives no hint (not library, downloads or recycle bin); set `LIBRARY_DIRS` / `DOWNLOAD_DIRS` / `RECYCLE_DIRS` | 0 |

**Needs a decision** = DUPLICATE_COPY, DUPLICATE_HEADER, DOWNLOAD_UNMATCHED, DOWNLOAD_OTHER_RELEASE, LEFTOVER, ORPHAN, BIN_OLD, OTHER and NOT_IN_PLEX_FOLDER (a whole folder Plex cannot see is yours to decide; a single file Plex has not indexed, NOT_IN_PLEX, is only a note). Everything else is fine and shown under *Everything fine*. Each download row also carries its client's state ("Deluge: seeding, ratio 1.31, 3 days"; Deluge, NZBGet, qBittorrent, rTorrent, Transmission and SABnzbd are read, and with Sonarr or Radarr connected they are found on the arrs' own Download Clients page) and a **can go now** chip when your seeding rule (`SEED_MIN_RATIO` and `SEED_MIN_DAYS`, both reached by default; `SEED_RULE=either` relaxes it) is met or nothing holds the file.

### 📺 What Plex shows (P2.5)

With Plex's address and token set (`PLEX_URL`, `PLEX_TOKEN`, or the Plex block on the Setup tab), each nightly scan asks Plex two things: which folders its film and show libraries look in, and which files it has indexed. Library files are then compared with that answer. Only library files: downloads, recycle bins and unsorted folders are never judged by Plex.

- A file that already has a more specific verdict (a real copy, two names for one file) keeps it; what Plex says is added to its evidence.
- Music and photo libraries are skipped. A video inside one of their folders is left alone, and About names the skipped libraries.
- Nothing is guessed from half an answer. If Plex is down, refuses the token, or a single page of a single library fails, no file gets a Plex verdict that night, and About says "Plex could not be read" with the reason.
- If Plex sees your files under another path than Dedupearr does, set `PLEX_PATH_MAP` (`plexpath=ourpath`). Until then nothing is compared, and About says so.
- The token travels in a request header only. It is never written to a log, an address or the page.
- Dedupearr only reads Plex. It never starts a scan there or changes anything.

## 📄 The pages

- **Files**: lead sentence (library size, waste, undecided downloads), five tiles that double as filters, filter chips, a row of kind buttons (TV shows, Anime, Movies, Adult, Sports, Music, Other) whose counts follow the filter, search, one row per file with its kind tag, chip, sentence, evidence, size, wasted space and an Ignore button.
- **Space**: one bar for the share (library · waste · to decide · other · free) a table of every verdict with count and space, and a table per kind of media (files, need a decision, wasted).
- **Setup** (P4.1): the shared-mount test ("Can a library file and a download be one file?", from Dedupearr's own mounts and each arr's import folders, with the one-line change when the answer is no), one block per arr and download client with address, user, password or key (never sent back), source, last answer, **Save** and **Test**, and what the arrs list on their Download Clients page.
- **Settings** (P1.7): the rules (recycle-bin days, seeding ratio, days and rule, smallest file, scan time, library/download/recycle folders and folder names), saved in the config folder's database; a value set on the container wins and is locked; fixes, scanned folders and holding days stay container-only.
- **About**: health (last scan, errors in the last 24 hours, read-only reminder), settings in force, scan history with durations, recent errors, log.

## 🔘 The buttons

| Button | Where | What it does |
|---|---|---|
| Scan now | header | starts a scan; the header shows phase and file count while it runs |
| Ignore / Un-ignore | a row that needs a decision | hides the row from Needs a decision (it stays under Everything with an *ignored* chip) and brings it back |
| evidence | under every sentence | shows the inodes, link counts, sizes, checksum result and the other file's path |
| Preview fix | a fixable row | shows the exact steps (relink / remove through the client / holding bin / empty an old bin) and the space freed; a dry run by default (`FIX_MODE=dryrun`), logged in `/api/actions` |
| Delete all now | Actions tab, above the holding bin | deletes every file in the holding bin for good, without waiting for its days; asks twice and says it cannot be undone; a file that changed inside the bin is left alone |

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
| `GET /api/settings` / `POST /api/settings` `{"set": {NAME: value}, "reset": [NAME]}` | every setting with its source and lock (passwords and keys never sent back) / save; refused for a value set on the container, and without a sign-in from anywhere but your own network |
| `GET /api/setup` / `POST /api/setup/test` `{"name": "Sonarr"}` | the shared-mount test, the arrs' client lists and each connection's last answer / ask one app with what is saved, read-only |
| `GET /health` | 200 when the last scan is recent and succeeded, else 503 |
