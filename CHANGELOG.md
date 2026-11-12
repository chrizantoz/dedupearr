# Changelog

All dates 2026. Image: `ghcr.io/chrizantoz/dedupearr`.

## Unreleased — phase 1, scan and tell (2026-11-11)

- First version. Scans the library and download folders, groups names that share one inode, confirms identical copies with checksums, judges recycle bins, and explains every file in one plain sentence with its evidence.
- Files, Space and About pages; nightly scan; Scan now; Ignore.
- New verdict from the first real-library run: "Stored twice, header differs": same size, middle and end identical, only the first megabyte differs, which is a language tag or title edited in place on one copy (Auditarr does this). Relinking such a pair is not safe, and the sentence says so.
- Read-only: nothing is moved, linked or deleted.
- Tests: fake disk, verdicts, plain language, API contract, UI bot, data fuzzer, button lint; CI builds and scans the image.
