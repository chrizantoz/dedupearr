# Installing Dedupearr (first time)

You need: a machine that runs Docker (Unraid, a NAS, a Linux box) and the share where your arr apps and your torrent client keep their files. Nothing else is connected in phase 1. Two minutes.

## The one thing to get right: one mount for library and downloads

Dedupearr tells a hardlink from a copy by looking at the files themselves, so it must see your library **and** your download folder through **one** mount. On most setups that is one share such as `/mnt/user/media` (Unraid) or `/srv/data` (Linux) that holds `media/` and `torrents/` side by side; mount that share at `/data` inside the container, read-only.

If your library and your downloads live on two different shares, hardlinks between them cannot exist, so every import is a copy. Dedupearr will show that as "Stored twice" on every file. The fix is in the arr guides: one share, two folders ([TRaSH Guides: hardlinks](https://trash-guides.info/File-and-Folder-Structure/Hardlinks-and-Instant-Moves/)).

## Unraid

1. Docker tab → **Add Container** → at the bottom, paste the template URL: `https://raw.githubusercontent.com/chrizantoz/dedupearr/main/docs/unraid-template.xml`.
2. Set **TZ**, check the **Data** path (the share that holds both library and downloads, read-only), leave everything else.
3. Apply, then open the Web UI (port 3092). The first scan starts on its own; 15,000 files take under a minute.
4. Read the sentence at the top of **Files**: it says how big your library is and how much is wasted.

If Dedupearr calls a folder **Unsorted folder**, it could not tell from the name whether it is library, downloads or a recycle bin. Add the folder to `LIBRARY_DIRS`, `DOWNLOAD_DIRS` or `RECYCLE_DIRS` (comma-separated, container paths such as `/data/films`) and it is judged on the next scan.

## Docker compose (any Linux / NAS)

```bash
mkdir dedupearr && cd dedupearr
curl -O https://raw.githubusercontent.com/chrizantoz/dedupearr/main/docs/docker-compose.yml
# edit the data line: /path/to/your/share:/data:ro
docker compose up -d
```

Open `http://<host>:3092`.

## Running next to Auditarr

Both apps read the same share and do not interfere in phase 1. When Dedupearr's fixes arrive (phase 3) the two apps coordinate: Dedupearr reads Auditarr's actions log, treats Auditarr's trash folder as a recycle bin, and never relinks a file whose header Auditarr edited. Give Dedupearr its own port (3092), its own config folder and a scan time an hour away from Auditarr's run.

## Updating

- **Unraid:** Docker tab → check for updates → **update** on Dedupearr. The database and the ignore list live in the config folder and survive.
- **Docker compose:** `docker compose pull dedupearr && docker compose up -d dedupearr`.

## After the first scan

- The **Files** tab opens on *Needs a decision*: second copies, downloads with no match, old recycle-bin files and unsorted folders. Everything that is fine is one filter away.
- Each row has one plain sentence and an **evidence** link that shows the inodes, link counts, sizes and checksums behind it.
- Phase 1 has no fix buttons. *Ignore* hides a row you have decided about. Act on the rest by hand if you want to; when phase 3 arrives, every fix runs as a dry run first and can be undone for 14 days.

The full tour of the page is in [USER-GUIDE.md](USER-GUIDE.md).
