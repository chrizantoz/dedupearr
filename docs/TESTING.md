# How Dedupearr is tested

Same approach as Auditarr: bots that behave like a careful user and a careless network, a fake disk with every situation planted, and gates that stop a bad build before anyone sees it. Everything below runs on every push (`.github/workflows/ci.yml`); the whole run takes about four minutes.

| | Category | What runs | Catches |
| --- | --- | --- | --- |
| 📏 | **Code quality** | `py_compile`, `pyflakes`, `ruff` (pycodestyle, pyflakes, bugbear, simplify) | unused imports, undefined names, mutable defaults, except-pass, dead code |
| 🧪 | **Backend tests** | `tests/verdicts.py` on the fake disk (`tests/fake_disk.py`), `tests/plain_language.py`, `tests/api.py` | a wrong verdict, a sentence with a raw code or a path in it, an endpoint that changed shape |
| 🎲 | **Fuzzing** | `tests/fuzz_disk.py` (random folder trees; the verdict rules are checked as invariants), `uibot/fuzz_ui.js` (hundreds of mangled API answers fed to the page) | a crash on an unexpected disk layout, a page that breaks on a half answer |
| 🌪️ | **Chaos** | `tests/chaos.py`: unlistable folder, unreadable file during checksumming, missing scan root, broken symlink, empty root | a scan that dies on one bad folder, a guess instead of a careful verdict |
| 🧬 | **Mutation** | `tests/mutation_check.py`: eight deliberate bugs planted one at a time (hardlinks ignored, waste not counted, bins never expire, …); the suite must fail each time | a test suite that would not notice a real regression |
| ⏱️ | **Performance budget** | `tests/perf_budget.py`: judging 60,000 files under 8 s, the page's state under 4 s | a quadratic loop that only shows on a big library |
| 🤖 | **UI bot** | `uibot/ui_bot.js` in jsdom with recorded real answers (`uibot/fixtures/`): every filter, tile, search, Ignore, Scan now, Space, About; checks are data-relative | a button that does nothing, a count that does not match the data, a raw code on screen |
| 🧩 | **Edge worlds** | `uibot/edge_fixtures.py` → tidy library, empty library, 3,000 rows, app unreachable, failed scan; the UI bot runs on each | a page that only works on a messy library |
| 🔍 | **Bot lints** | `uibot/lint_checks.py` (no fixed counts in checks), `uibot/lint_buttons.py` (every button is pressed by some bot) | a check that passes by accident, a button nobody tests |
| 🌐 | **Real browser** | `uibot/browser_bot.js`: Playwright Chromium, phone and desktop, screenshots, the tasks a person would try ("how much can I get back?", "which files are stored twice and why?", "stop showing this one") | layout that scrolls sideways on a phone, a flow a real person gets stuck in |
| 🛡️ | **Image scan** | `docker build` + Trivy, CRITICAL and HIGH, fail the build | a known vulnerability in the base image |
| 📦 | **Delivery** | image pushed to GHCR only when every step above is green; tagged `latest`, the commit, and the version on a release | shipping an untested build |

## Running it yourself

```bash
python3 tests/plain_language.py && python3 tests/verdicts.py && python3 tests/api.py
python3 tests/fuzz_disk.py 25 && python3 tests/chaos.py && python3 tests/perf_budget.py && python3 tests/mutation_check.py all
cd uibot && npm install && node ui_bot.js ../ui.html fixtures && node fuzz_ui.js ../ui.html fixtures 300
python3 lint_checks.py && python3 lint_buttons.py && python3 edge_fixtures.py && for c in tidy empty huge down errors; do node ui_bot.js ../ui.html fixtures-edge/$c; done
npx playwright install chromium-headless-shell && node browser_bot.js ../ui.html fixtures          # screenshots land in uibot/screens/
node browser_bot.js ../ui.html http://your-server:3092                                            # the same personas against a running Dedupearr (reads only; ALLOW_WRITES=1 to let it press Ignore and undo it)
```

## Rules the tests enforce

- A check never hard-codes a count of three or more without a `// fixed:` reason; it derives the number from the data.
- Every sentence a person reads ends with a full stop, names no raw code and no path, and says what happens next.
- A verdict exists only when the evidence supports it: a hardlink needs a shared inode across library and download, "Stored twice" needs identical content, "Looks alike" needs content that differs.
- A second scan of an unchanged disk changes nothing.
- The scan never dies because one folder or file misbehaves; it logs the problem, judges what it can, and the About page lists the errors.

## Still to come (phase 2 and later)

- 🧫 A fake stack of download clients and arrs with recorded real API shapes, like Auditarr's `tests/mock_stack.py`.
- 🌙 Nightly UI bot against the production container, with the bot built from the same commit (Auditarr's drift guard).
- 🚦 Staged soak before promotion, and the public demo as a nightly canary.
