# Dedupearr

**The truth about your storage.** For every video file under your media and download folders, Dedupearr tells you what the file *is*:

- **Real copy**: the library file and the download are one file with two names (a hardlink). Seeding costs nothing.
- **Stored twice**: the library file and the download are two separate files with identical content. That is wasted space.
- **Download without a match**: nothing in the library matches it. A leftover from an upgrade, an orphan, or still seeding.
- **Old recycle-bin file**: sitting in a recycle bin longer than your rule.
- **Looks alike**: same size as a library file but different content. Not a duplicate.

Every verdict comes with its evidence (inode, link count, size, checksum) in one plain sentence, and the first screen says how much space you can get back.

It is the companion to [Auditarr](https://github.com/chrizantoz/auditarr), which checks whether a file is *good*. Dedupearr checks whether a file is *real and needed*.

## Status: phase 1, read-only

| Phase | What it does | Status |
| --- | --- | --- |
| 1 · Scan and tell | Disk scan (inode, link count, size, checksum), verdicts, Files and Space pages, nightly scan | **this release** |
| 2 · Ask the apps | qBittorrent, Deluge, rTorrent, Transmission, Sonarr, Radarr: an unmatched download becomes *leftover*, *orphan* or *still seeding*; Auditarr's rewrites are recognised | next |
| 3 · Fix safely | Relink, remove the old torrent, empty the bin: dry run first, holding bin, 14-day undo, "fix all with this cause" | after 2 |
| 4 · Easy to install | Setup wizard with the shared-mount test, Unraid cache/array awareness, Unraid Apps template, public demo | after 3 |

Phase 1 never moves, links or deletes anything. It looks and tells.

## Run it

```yaml
services:
  dedupearr:
    image: ghcr.io/chrizantoz/dedupearr:latest
    container_name: dedupearr
    ports: ["3092:3092"]
    volumes:
      - /mnt/user/appdata/dedupearr:/config
      - /mnt/user/media:/data:ro          # ONE mount that holds both the library and the download folders
    environment:
      RECYCLE_DAYS: "30"
      SCAN_TIME: "04:30"
    restart: unless-stopped
```

Open `http://your-server:3092`. The first scan starts on its own; after that it runs every night at `SCAN_TIME`.

**The one rule that matters:** mount the library and the download folders through *one* path (`/data` above), the same way the arr guides tell you to. If they are two mounts, a hardlink between them is impossible and Dedupearr cannot see that two names are one file.

### Settings (environment)

| Setting | Default | Meaning |
| --- | --- | --- |
| `SCAN_ROOTS` | `/data` | Comma-separated folders to scan |
| `LIBRARY_DIRS`, `DOWNLOAD_DIRS`, `RECYCLE_DIRS` | empty | Explicit folders when the names below do not fit |
| `LIBRARY_HINTS` | `media,library,movies,tv,shows,series,anime,…` | Folder names that mean "library" |
| `DOWNLOAD_HINTS` | `torrents,downloads,complete,incomplete,usenet,…` | Folder names that mean "downloads" |
| `RECYCLE_HINTS` | `.recycle,.trash,recycle,trash,#recycle,…` | Folder names that mean "recycle bin" |
| `RECYCLE_DAYS` | `30` | Bin files older than this are waste |
| `MIN_SIZE_MB` | `50` | Smaller files are not judged |
| `HASH_BUDGET_MB` | `20000` | Full checksums per scan; beyond it duplicates are called "very likely" from samples |
| `SCAN_TIME` | `04:30` | Nightly scan time |
| `PORT` | `3092` | Web port |

Nothing here is a secret. No keys are stored in phase 1.

## Try it without a library

```bash
python3 tests/fake_disk.py /tmp/dedupearr-fake
CONFIG_DIR=/tmp/dedupearr-cfg SCAN_ROOTS=/tmp/dedupearr-fake MIN_SIZE_MB=1 python3 dedupearr.py --scan
```

The fake disk holds one of every situation: a real hardlink, an identical second copy, an unmatched download, a lookalike, an old and a recent recycle-bin file, two names inside the library, and an unsorted folder.

## How it is tested

Same approach as Auditarr: the backend tests run against a fake disk with every situation planted; `tests/api.py` starts the real server and checks every endpoint, then records its answers as fixtures; the UI bot (`uibot/ui_bot.js`) loads the page in a headless browser, presses every button and checks the outcome against those answers; the fuzzer (`uibot/fuzz_ui.js`) feeds the page hundreds of mangled answers and allows no error; `lint_buttons.py` fails when a button exists that no bot presses. CI runs all of it on every push and builds the image.

## Licence

GPL-3.0. Source: https://github.com/chrizantoz/dedupearr
