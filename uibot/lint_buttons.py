"""Every button on the page is pressed by a bot. A button nobody presses is a button nobody tests.
Run: python3 uibot/lint_buttons.py   Exit 1 when a button id or data-hook has no bot referencing it."""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ui = open(os.path.join(HERE, "..", "ui.html"), encoding="utf-8").read()
bots = "".join(open(os.path.join(HERE, f), encoding="utf-8").read() for f in os.listdir(HERE) if f.endswith(".js"))
ids = set(re.findall(r'<button[^>]*\sid="([^"]+)"', ui)); hooks = set(re.findall(r"b\.dataset\.(\w+)", ui.split("<script>")[1]))
SKIP = {}   # hook -> reason, when a hook is deliberately untested
missing = [f"button #{i}" for i in sorted(ids) if i not in bots] + [f"data-{h}" for h in sorted(hooks) if h not in SKIP and f"data-{h}" not in bots and f"dataset.{h}" not in bots]
print(f"every button is pressed by a bot: {len(ids)} ids, {len(hooks)} action hooks" if not missing else "untested: " + ", ".join(missing))
sys.exit(1 if missing else 0)
