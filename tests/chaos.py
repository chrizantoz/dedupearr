"""Chaos: things that go wrong on a real server must not stop the scan or produce a wrong verdict. A folder we may not list, a file we may
not read while fingerprinting, a scan root that is not mounted, a broken symlink, an empty root. Run: python tests/chaos.py   Exit 1 on failure."""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
TMP = tempfile.mkdtemp(prefix="dedupearr-chaos-"); DATA = os.path.join(TMP, "data"); MISSING = os.path.join(TMP, "not-mounted")
os.environ.update(CONFIG_DIR=os.path.join(TMP, "config"), SCAN_ROOTS=f"{DATA},{MISSING},{os.path.join(TMP, 'empty')}", MIN_SIZE_MB="1", HASH_BUDGET_MB="100")
os.makedirs(os.path.join(TMP, "empty"))
import fake_disk  # noqa: E402
import dedupearr as d  # noqa: E402
results = []
def check(name, cond, info=""): results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:220]}]"))
P = fake_disk.build(DATA)
os.symlink(os.path.join(DATA, "nowhere.mkv"), os.path.join(DATA, "torrents", "broken-link.mkv"))
locked_dir = os.path.join(DATA, "media", "locked"); os.makedirs(locked_dir); fake_disk.blob(os.path.join(locked_dir, "Secret.2020.1080p.mkv"), 2, "secret")
root_user = os.geteuid() == 0
if not root_user: os.chmod(locked_dir, 0); os.chmod(P["dup_dl"], 0)   # cannot list / cannot read (root bypasses both, so skip there)
d.LOG.clear()
try:
    r = d.scan(); c = d.db(); S = d.state(c); by = {i["path"]: i for i in S["items"]}
    check("the scan finished although a root is missing, a folder is unlistable and a file is unreadable", r is not None and S["last_run"]["note"] == "ok", S["last_run"])
    check("the missing root is reported, not fatal", any("not a folder" in l for l in d.LOG), d.LOG[-5:])
    check("the broken symlink is skipped silently", not any("broken-link" in p for p in by))
    check("files that could be judged were judged", by[P["real_lib"]]["code"] == "REAL_COPY" and by[P["unmatched"]]["code"] == "DOWNLOAD_UNMATCHED")
    if not root_user:
        check("the unlistable folder is logged and the rest of the scan goes on", any("cannot list" in l for l in d.LOG) and not any("Secret" in p for p in by), d.LOG[-5:])
        check("the unreadable duplicate is logged and gets a careful verdict instead of a guess", any("could not read" in l for l in d.LOG) and by[P["dup_dl"]]["code"] in ("DOWNLOAD_UNMATCHED", "LOOKALIKE"), (by[P["dup_dl"]]["code"], d.LOG[-5:]))
        os.chmod(locked_dir, 0o755); os.chmod(P["dup_dl"], 0o644)
    a = d.about(c)
    check("About warns about the missing root in plain words", any("not mounted" in h["text"] for h in a["health"]), a["health"])
    check("About lists the recent errors", isinstance(a.get("errors"), list) and any("cannot" in e["text"] or "not a folder" in e["text"] for e in a["errors"]), a.get("errors"))
    c.close()
except Exception as e:
    check("the scan did not crash", False, repr(e))
bad = results.count(False); print(f"\n{len(results) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
