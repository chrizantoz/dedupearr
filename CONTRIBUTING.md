# Contributing to Dedupearr

Thanks for looking under the hood. Dedupearr is small on purpose: one Python file, one HTML page, a SQLite file, and a test harness that is bigger than the app. Here is how to work on it without breaking the promises it makes.

## 🧭 The three rules

1. **A verdict needs evidence.** A hardlink is a shared inode across the library and the download folder. "Stored twice" needs identical content, confirmed by checksum. Same size with different content is "Looks alike", never waste. If you add a verdict, add the rule that proves it and a fuzz invariant that checks it.
2. **Nothing is changed without a dry run and an undo.** Phase 1 changes nothing at all. When fixes arrive (phase 3) every one runs as a dry run first, moves files to a holding bin instead of deleting, and can be undone for 14 days. A pair whose header differs is never relinked.
3. **Plain language first.** Every verdict gets a name a non-technical person understands, every file one sentence that says what it is and what would change, every button a verb. Codes and paths stay under *evidence*.

## 🗂️ Where things live

| what | where |
|---|---|
| the whole backend: scanner, verdicts, scheduler, HTTP API | `dedupearr.py` |
| the whole page | `ui.html` |
| plain names and sentences | `LABEL` and `sentence()` in `dedupearr.py` |
| the fake disk with every situation | `tests/fake_disk.py` |
| tests | `tests/` (see `docs/TESTING.md` for what each one proves) |
| UI bots and fixtures | `uibot/` |
| docs for users | `docs/USER-GUIDE.md`, `docs/INSTALL.md`, `FEATURES.md` |
| roadmap | `ROADMAP.md` (pick an unticked item or open an issue first for anything bigger) |

## 🧪 Before you open a pull request

```bash
python3 tests/plain_language.py && python3 tests/verdicts.py && python3 tests/api.py
python3 tests/fuzz_disk.py 25 && python3 tests/chaos.py && python3 tests/perf_budget.py && python3 tests/mutation_check.py all
cd uibot && npm install && node ui_bot.js ../ui.html fixtures && node fuzz_ui.js ../ui.html fixtures 300 && python3 lint_checks.py && python3 lint_buttons.py && cd ..
```

CI runs all of that plus the edge worlds, a real-browser pass with screenshots, ruff and an image scan. A pull request needs the `test` job green; the maintainer merges with squash.

## ✍️ Adding a verdict

1. Decide it in `judge()` with a code, the evidence string and whether it counts as waste.
2. Give it a plain name in `LABEL` and a sentence in `sentence()`; add it to `NEEDS_YOU` if a person has to decide.
3. Plant the situation in `tests/fake_disk.py` and assert it in `tests/verdicts.py`; add a golden sentence to `tests/plain_language.py`; add its rule to `tests/fuzz_disk.py`; add a mutant to `tests/mutation_check.py` that breaks it.
4. Give it a tone in the page (`TONE` map) and, if it needs a filter, a chip.
5. Describe it in `FEATURES.md`, `docs/USER-GUIDE.md` and `CHANGELOG.md`.

## 🐛 Reporting problems

Use the issue templates. For anything security-related, use the private report on the Security tab instead of an issue.

## 📄 Licence

GPL-3.0. By contributing you agree your changes are licensed the same way.

## 🚀 Releasing

`scripts/release.sh vX.Y.Z` runs the whole checklist (checks, notes from the changelog, screenshots, tag, GitHub release, boot check). `docs/RELEASING.md` explains each step.
