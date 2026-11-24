# 📘 Dedupearr user guide

A plain-language tour: what the app does, what you see on the page, and what every button does. No coding needed. If you only read one section, read **Your daily routine**.

## 📖 Five words you'll see

- **Library**: the folder your media server plays from (`media/movies`, `media/tv`). Sonarr and Radarr put finished files here.
- **Download folder**: where your torrent client writes files while it downloads and seeds (`torrents`, `downloads`).
- **Hardlink**: two names for one file on disk. When your arr imports a download as a hardlink, the library file and the seeding torrent are the *same* file, so it takes space once. This is what every arr guide tells you to set up, and it only works when both folders live on one share.
- **Copy**: the arr could not hardlink (usually because the two folders are on different shares or different mounts), so it copied. Same movie, twice the space.
- **Evidence**: the numbers behind a verdict. An *inode* is a file's id on disk; two names with the same inode are one file. A *checksum* is a fingerprint of the content; two files with the same checksum are identical.

## 💾 What Dedupearr does

Every night it walks every video file in your library and your download folder and works out what each file *is*: the real copy, a hardlink that costs nothing, a second copy that wastes space, a download nothing in the library matches, or an old recycle-bin entry. It shows the evidence (inode, link count, size, checksum) in one sentence you can read without knowing what an inode is.

Out of the box it changes nothing on disk: fixes run as a dry run that shows their steps, until you switch them on.

## ☕ Your daily routine

1. 👀 Open the page. The first sentence says how big your library is and how much is wasted. If it says nothing is wasted, you are done.
2. 🧐 Look at **Needs a decision**: the rows Dedupearr cannot settle for you. Read the sentence, open **evidence** if you want the numbers.
3. 🔘 Press **Ignore** on the rows you have decided about. In phase 1 you make the change yourself (delete the old torrent in your client, empty the bin); from phase 3 the row carries the button.

## 🚦 Reading a verdict

Each row is one file with a coloured chip:

| chip | meaning | what to do |
|---|---|---|
| 🟢 **Real copy** | the library file and the download are one file with two names (a hardlink); seeding costs no space | nothing |
| 🟢 **Only copy** | one copy, in the library | nothing |
| 🟢 **Two names, one file** | two names inside the library share one file | nothing |
| 🟢 **Seeding name** | the download-folder name of a Real copy | nothing (it is listed under its library row) |
| 🟡 **Stored twice** | the library file and the download are two separate files with identical content | the download can go once seeding is done; relinking (phase 3) gives the space back and keeps seeding |
| 🟡 **Stored twice, header differs** | same, except the first megabyte: a language tag or title was edited on one copy (Auditarr does this) | the download can go once seeding is done; never relink these (the torrent would put the old header back) |
| 🟡 **Download without a match** | nothing in the library matches this download | a leftover from an upgrade, an orphan or still seeding; phase 2 asks the client and tells you which |
| 🟡 **Different release in library** | nothing identical, but the library has another release of the same title (same name and year, or same episode) | almost always a leftover from an upgrade: the download can go once seeding is done |
| 🟡 **Leftover from an upgrade** | Sonarr or Radarr imported this download, and the library has since replaced the file it became | it can go; the row says whether seeding is done |
| 🔴 **Orphan** | no download client knows this file and no arr imported it | nothing holds it; it can go |
| 🟢 **Seeding only** | your client seeds it and nothing in the library came from it (software, extras, torrents you keep); the sentence says when it is an extra or a disc image | nothing; it goes when you remove the torrent |
| 🟢 **Extra, not library** | an opening or ending without credits (NCOP, NCED), a trailer, sample or making-of, a disc image or software in a download folder, and nothing else is known about it | nothing; the arrs never import these, so it is neither waste nor a decision. Remove it in your client when you no longer want it |
| ⚪ **Looks alike** | same size as a library file but different content | not a duplicate; worth a look if you expected one copy |
| ⚪ **Old recycle-bin file** | in a recycle bin longer than your rule (30 days by default) | empty it |
| 🟢 **In the recycle bin** | in a recycle bin, within the rule | nothing yet |
| 🟡 **Outside Plex's folders** | only with Plex connected: a library file in a folder none of your Plex libraries looks in, so Plex never shows it | add the folder to a Plex library, or move the file into a folder Plex already has; press **Ignore** if the folder is meant to stay out of Plex. No space is wasted |
| ⚪ **Plex does not show it** | only with Plex connected: a library file inside a Plex folder that Plex has not indexed. Usually a name Plex cannot match, a trailer or other extra, or a scan that has not run | rename it the way Plex expects, or run a library scan in Plex. A note, not a decision; a file less than a day old is never called this |
| 🔴 **Unsorted folder** | the folder name does not say whether this is library, downloads or a recycle bin | tell Dedupearr with `LIBRARY_DIRS`, `DOWNLOAD_DIRS` or `RECYCLE_DIRS` |

Each row has one plain sentence that says what the file is and what would change. Download rows also show what the client says ("Deluge: seeding, ratio 1.31, 3 days"). Dedupearr reads Deluge, NZBGet, qBittorrent, rTorrent, Transmission and SABnzbd. Connect Sonarr or Radarr and it finds them on the arrs' own *Download Clients* page; a client that needs a password or API key still needs it typed in, because the arrs never share those. A row from rTorrent or Transmission whose seeding is done says **Remove it in rTorrent first**: Dedupearr reads those two but does not remove torrents from them yet, so it never moves a file they still expect to find and a **can go now** chip when your seeding rule is met, or when nothing holds the file at all. An **evidence** link underneath opens the numbers: the inodes, link counts, sizes and whether the checksums matched, plus the path of the other file when there is one.

## 🗂️ The tabs

- 💾 **Files**: the lead sentence, the five tiles (a tile is also a filter), the filter chips, the search box and the table.
- 📊 **Space**: one bar for the whole share: library, waste you can get back, downloads still to decide, other files, free. Under it, every verdict with its count and the space it holds.
- 🧺 **Actions**: the holding bin (every file a fix moved, why it went, the days it has left and a **Put back** button) and the actions log (every fix, dry run or done, with its result).
- 🧭 **Setup**: first, **Can a library file and a download be one file?** One sentence answers it, from what Dedupearr sees (are your library and downloads on one mount inside its container) and from each arr's own import history (does Sonarr take downloads from `/downloads` and put the library in `/tv`; inside Sonarr's container that usually means two mounts, so every import is a copy). When the answer is no, the line under it gives the one-line change, for example one `-v /mnt/user/data:/data` instead of two. Then one block per arr and download client: its address, its user, its password or API key (never shown back, only whether one is saved), where it comes from (the container, this page, or an arr's list), whether it answered on the last scan, **Save** and **Test**. Test asks the app with what is saved, reads only, and says what it found. A connection set on the container is shown and changed there. At the bottom, what your arrs list on their own Download Clients page, switched on or not.
- ⚙️ **Settings**: the rules: days a file may sit in a recycle bin, the seeding ratio and days and which of the two counts, the smallest file to judge, the nightly scan time, folders and folder names that mean library, downloads or recycle bin. Change one, press **Save changes**, and the next scan uses it (press **Scan now** to see it at once); **Use the default** puts one back. A rule set on the container wins and is shown locked. Fixes, the folders to scan and the holding-bin days are set on the container only.
- ℹ️ **About**: **Protect the page** (set a user and password so the page asks for a sign-in; tick **No sign-in on my own network** to skip it at home while your domain still asks, but not if your router forwards the port straight to Dedupearr), health lines (last scan, errors in the last 24 hours, the read-only reminder), the settings in force, every scan with its duration, recent errors and the log.

## 🔎 Filters above the list

**Needs a decision** (the default) · **Stored twice** · **No match** · **Old bin files** · **Unsorted** · **Not in Plex** (only with Plex connected: every library file Plex does not show) · **Everything fine** · **Everything**. The search box narrows by title, path or verdict as you type. The page shows at most 500 rows; narrow the filter or search for the rest.

Under the filters is a second row, one button per **kind of media**: 📺 TV shows · 🍥 Anime · 🎬 Movies · 🔞 Adult · 🏟️ Sports · 🎵 Music · 📦 Other. Each count follows the filter above it, so "Needs a decision" plus "Anime" shows only the anime files that need you. The page remembers the kind you picked. Every row also carries a small kind tag next to its title, and the **Space** tab totals files and wasted space per kind.

How Dedupearr knows the kind, strongest evidence first:

1. **Your arrs' library folders.** A file inside a Sonarr root folder is a TV show, inside Radarr's is a movie, Whisparr's is adult, Sportarr's is sports, Lidarr's is music. A series Sonarr marks as anime, or a root folder named "anime", makes it Anime.
2. **The library file it belongs to.** A download linked to, or identical to, a library file takes that file's kind.
3. **The arr that imported it.**
4. **The download client's label or category**, such as `tv-sonarr`, `radarr` or `anime`.
5. **Folder names**, such as `torrents/movies`.

Anything none of these explain is **Other**, for example a software download.

## 🔘 Buttons

- **Scan now** (header): starts a scan straight away; the header shows the phase and the file count while it runs, and the page refreshes itself.
- **Ignore** (on a row that needs a decision): hides the row from *Needs a decision*; it stays under *Everything* with an *ignored* chip and an **Un-ignore** button.
- **evidence** (under a sentence): shows the numbers behind the verdict.
- **Preview fix** (on a row that can be fixed): shows the exact steps the fix would take and the space it frees. In dry run, the default, that is all it does; nothing is moved. When fixes are switched on (`FIX_MODE=on`), the fixes that have passed their safety checks become real: today that is **Move to holding bin** on an orphan and **Remove torrent, move to holding bin** on a leftover or second copy whose seeding is done. Press it twice (the first press asks *Sure?*). The file moves into the holding bin; nothing is deleted. For a torrent, Dedupearr first asks the client again whether it is still past your seeding rule, then tells it to forget the torrent and keep its files; if the client refuses, the file goes straight back. A torrent that holds other large files is left alone. **Put back** returns the file, not the torrent. The other fixes (relink, empty an old bin) keep showing their plan as a dry run until their own checks pass.
- **Make them one file** (on a download that is an identical second copy of a library file, when fixes are switched on): compares the two files byte for byte, which takes a few minutes for a big file and shows its progress on the row. Then the download copy moves to the holding bin and its name becomes a hardlink to the library file, so the space is used once and a download client keeps seeding. Nothing waits for the seeding rule here, because nothing stops seeding. If the two files sit on different disks of a pool the link cannot be made; the copy goes straight back and the page says so. Put back on the Actions tab swaps the old copy back in.
- **Remove torrent, move its N files to holding bin** (on the rows of a torrent with several files, such as a season pack): the torrent goes as a whole or not at all. Every file in it must be a copy your library does not need or a hardlink to the library. The copies move to the holding bin, hardlinks stay, and the client forgets the torrent and keeps its files. While one of its files is in no library, the rows have no button and say what to decide first. Put back returns each file on its own, not the torrent.
- **Move all shown to the holding bin** (above the list, when fixes are switched on and two or more plain moves are on the page): moves every such file you see, one after the other, each through the same checks as its own button. Narrow the list first with a filter, a kind or the search box. It asks to be sure, then says how many moved and names any it left alone. Put back on the Actions tab undoes each one. A row that also removes a torrent is never included, because a removed torrent does not come back; press that row's own button.
- **Delete all now** (on the Actions tab, above the holding bin, while it holds files): deletes every file in the holding bin for good and frees the space at once, without waiting for the days to run out. It asks twice, because this is the one button that cannot be undone: Put back no longer works for a deleted file. A file that changed while it sat in the bin is left alone and its row says why. Each deletion is written in the actions log as yours.
- **Put back** (Actions tab): returns a held file to its old place. It refuses when something new already sits there, and it cannot bring back a file whose days have run out.

That is all of them.

## 🌙 What runs by itself every night

One scan at `SCAN_TIME` (04:30 by default), and one at start when the last scan is more than 20 hours old. A scan lists every video file, groups the names that share an inode, compares same-size library and download files by checksum, judges the recycle bins, and writes the verdicts. Checksums are remembered per file, so a file is hashed once in its life. Nothing is moved, linked or deleted, with one exception: files that have waited `HOLD_DAYS` (14 by default) in the holding bin are deleted at the start of the scan, and only if the file there is still the one that went in.

## 📱 The morning message

With an [ntfy](https://ntfy.sh) topic set (`NTFY_URL`), Dedupearr sends one message to your phone after the nightly scan. It says how many files need you and how much space can come back, which kinds are the biggest, how much can go right now, what was moved, put back or deleted for good since yesterday, and what waits in the holding bin. The last line is either "No errors since yesterday" or the trouble by name: a failed scan, a download client that could not be read, errors. If you also run Auditarr, set Dedupearr's address in Auditarr's settings instead: its daily message then carries Dedupearr's lines too, and your phone gets one message, not two.

## 🧺 The holding bin

A fix never deletes. It moves the file to `HOLDING_DIR` (by default `.dedupearr-holding` at the top of your first scan folder), under the day it went and its full old path, so you can always see where it came from. The move is a rename inside the same share: instant, no copying, no extra space. Dedupearr refuses the move when the file changed since the scan, when the bin would be on another disk, or when the share is mounted read-only (the default template mounts it read-only; switch the **Data** path to read-write before you turn fixes on). The scan never counts the holding bin as library or downloads.

## 📺 What Plex shows

Dedupearr can ask Plex which of your library files it actually shows. On the **Setup** tab, type Plex's address (such as `http://192.168.1.10:32400`) and its token into the Plex block, press **Save**, then **Test**: it answers with the names of your Plex libraries. (To find the token: in Plex, open any film's **Get Info → View XML**; the address of the page that opens ends in `X-Plex-Token=…`.) The next scan then compares your library with Plex:

- **Outside Plex's folders** means the file sits in a folder none of your Plex libraries looks in. It is in your library, it takes space, and nobody can watch it in Plex. This is almost always a whole folder, so it is listed under *Needs a decision*: add the folder to a Plex library, or press **Ignore** on files that are meant to stay out of Plex.
- **Plex does not show it** means the folder is one Plex looks in, but this file is not in Plex's list. Usually Plex could not match the name, or the file is a trailer or other extra, which Plex files under its film. It is a note, not a decision. A file less than a day old is never called this, because Plex may not have scanned yet.

Neither wastes space, and neither has a fix button: the change is made in Plex, or by renaming or moving the file. The **Not in Plex** filter lists both kinds.

Dedupearr never guesses. If Plex is switched off, refuses the token, or answers only in part, no file is called anything about Plex that night, and the **About** tab says *Plex could not be read* with the reason. If About says none of Plex's folders is a folder Dedupearr can see, Plex sees your files under another path (for example `/movies` where Dedupearr sees `/data/media/movies`): set `PLEX_PATH_MAP` on the container, as `plexpath=ourpath`. Music and photo libraries are not read.

## 🔒 Sharing the files with Auditarr

Auditarr changes library files on purpose: it sets default tracks, labels audio and subtitles, and sometimes rewrites a whole file. The download copy of such a file is then no longer identical, and Dedupearr shows it as **Stored twice, header differs** or as a **Leftover**. With Auditarr's address set (`AUDITARR_URL`), the row says so in words: what Auditarr did and on which day. Such a download is only still needed for seeding and can go once your seeding rule is met; it is never relinked, because the torrent would undo Auditarr's change. Files in Auditarr's trash show as **In Auditarr's trash**: Auditarr deletes them for good or puts them back by itself, so Dedupearr leaves them alone and does not count them as library.

Auditarr rewrites files (rewrap, track labels, time bar) and Dedupearr moves them. So that the two never touch one file at the same moment, each leaves a small marker in a `.fix-locks` folder at the top of your media share while it works, and removes it when it is done. If you press a fix on a file Auditarr is busy with, nothing is changed and the answer says so: wait a few minutes and press again. **Move all shown** leaves such a file alone and moves the rest. A marker older than 6 hours is ignored, so a crash never blocks a file for good. Both apps must see the same folder; with the default folders they do, otherwise set `LOCK_DIR` in both.

**Who may change settings?** With **Protect the page** on, whoever is signed in. Without a sign-in, only a browser on your own network that opens the page's address and port directly, never through a domain or reverse proxy, so nobody outside can point Dedupearr at another server. A new address for an app forgets the password saved for the old one, so a password is never sent somewhere it was not typed for. Settings live in the database in the config folder and survive updates.

## 💬 Frequently asked

**Why is almost everything "Stored twice"?** Your arr copies instead of hardlinking, usually because the library and the download folder are two different mounts in the arr's container. The fix is one share with two folders, as the arr guides describe. Dedupearr phase 2 will name the cause on the row.

**A torrent is still seeding; can I delete the "Stored twice" download?** Not while it seeds: the client needs the file. Phase 3's relink keeps seeding and frees the space. Until then, leave it or remove the torrent in your client when its seeding time is up.

**Why does it say "header differs"?** Auditarr (or you, with a tag editor) changed the audio-language tag or the title inside the library file. The video is the same, the first megabyte is not. Those two files must never be relinked: the torrent client would write the old header back over the fixed library file.

**What does "Download without a match" mean?** No library file has the same content. It may be an old release that an upgrade replaced, a download that never imported, or something you keep only for seeding. Phase 2 asks the download client and the arrs and says which.

**What is "your seeding rule"?** Two numbers and a word in the settings: `SEED_MIN_RATIO` (1.0 by default), `SEED_MIN_DAYS` (14) and `SEED_RULE` (`both`: the ratio and the days must both be reached, which is what private trackers expect; `either`: one of them is enough, fine for public trackers). The default, `auto`, uses `both` for private torrents and `either` for public ones; Deluge and qBittorrent say which is which. Set them to what your trackers expect. A row still seeding says where the Preview fix button would be when it can go ("Still seeding · can go in about 4 days"). Usenet downloads have no seeding, so they can go as soon as the arr has imported them.

**Does it slow my server down?** A scan of 15,000 files takes under a minute and reads only file metadata plus a few megabytes of each file that looks like a copy. Full checksums are capped per night by `HASH_BUDGET_MB`.

**Where is the data?** One SQLite file in the config folder. Delete it and the next scan rebuilds everything; only your ignore list is lost.
