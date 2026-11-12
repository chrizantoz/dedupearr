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

Phase 1 changes nothing on disk. It looks and tells.

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
| ⚪ **Looks alike** | same size as a library file but different content | not a duplicate; worth a look if you expected one copy |
| ⚪ **Old recycle-bin file** | in a recycle bin longer than your rule (30 days by default) | empty it |
| 🟢 **In the recycle bin** | in a recycle bin, within the rule | nothing yet |
| 🔴 **Unsorted folder** | the folder name does not say whether this is library, downloads or a recycle bin | tell Dedupearr with `LIBRARY_DIRS`, `DOWNLOAD_DIRS` or `RECYCLE_DIRS` |

Each row has one plain sentence that says what the file is and what would change. An **evidence** link underneath opens the numbers: the inodes, link counts, sizes and whether the checksums matched, plus the path of the other file when there is one.

## 🗂️ The tabs

- 💾 **Files**: the lead sentence, the five tiles (a tile is also a filter), the filter chips, the search box and the table.
- 📊 **Space**: one bar for the whole share: library, waste you can get back, downloads still to decide, other files, free. Under it, every verdict with its count and the space it holds.
- ℹ️ **About**: health lines (last scan, errors in the last 24 hours, the read-only reminder), the settings in force, every scan with its duration, recent errors and the log.

## 🔎 Filters above the list

**Needs a decision** (the default) · **Stored twice** · **No match** · **Old bin files** · **Unsorted** · **Everything fine** · **Everything**. The search box narrows by title, path or verdict as you type. The page shows at most 500 rows; narrow the filter or search for the rest.

## 🔘 Buttons

- **Scan now** (header): starts a scan straight away; the header shows the phase and the file count while it runs, and the page refreshes itself.
- **Ignore** (on a row that needs a decision): hides the row from *Needs a decision*; it stays under *Everything* with an *ignored* chip and an **Un-ignore** button.
- **evidence** (under a sentence): shows the numbers behind the verdict.

That is all of them. Phase 3 adds the fix buttons, each with a dry run and a 14-day undo.

## 🌙 What runs by itself every night

One scan at `SCAN_TIME` (04:30 by default), and one at start when the last scan is more than 20 hours old. A scan lists every video file, groups the names that share an inode, compares same-size library and download files by checksum, judges the recycle bins, and writes the verdicts. Checksums are remembered per file, so a file is hashed once in its life. Nothing is moved, linked or deleted.

## 💬 Frequently asked

**Why is almost everything "Stored twice"?** Your arr copies instead of hardlinking, usually because the library and the download folder are two different mounts in the arr's container. The fix is one share with two folders, as the arr guides describe. Dedupearr phase 2 will name the cause on the row.

**A torrent is still seeding; can I delete the "Stored twice" download?** Not while it seeds: the client needs the file. Phase 3's relink keeps seeding and frees the space. Until then, leave it or remove the torrent in your client when its seeding time is up.

**Why does it say "header differs"?** Auditarr (or you, with a tag editor) changed the audio-language tag or the title inside the library file. The video is the same, the first megabyte is not. Those two files must never be relinked: the torrent client would write the old header back over the fixed library file.

**What does "Download without a match" mean?** No library file has the same content. It may be an old release that an upgrade replaced, a download that never imported, or something you keep only for seeding. Phase 2 asks the download client and the arrs and says which.

**Does it slow my server down?** A scan of 15,000 files takes under a minute and reads only file metadata plus a few megabytes of each file that looks like a copy. Full checksums are capped per night by `HASH_BUDGET_MB`.

**Where is the data?** One SQLite file in the config folder. Delete it and the next scan rebuilds everything; only your ignore list is lost.
