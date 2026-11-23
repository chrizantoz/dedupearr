"""Bot checks must follow the data, not a fixed count. A check like `.length > 5` passes on a messy library and fails on a tidy one.
Flags `check(` lines in the bots that compare a length/size/count with a literal of 3 or more, unless the line says why with `// fixed:`.
A second rule, for the Python tests (#32): what a failed check prints must never be an object that holds a password or key (a secret
setting, a connection from ARRS/CLIENTS, an Authorization header). Say in words what went wrong instead.
Run: python3 uibot/lint_checks.py   Exit 1 on findings."""
import ast, glob, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); bad = []
for name in ("ui_bot.js", "browser_bot.js"):
    for i, line in enumerate(open(os.path.join(HERE, name), encoding="utf-8"), 1):
        if "check(" not in line or "// fixed:" in line: continue
        for m in re.finditer(r"(\w+(?:\.(?:length|size|count))?)\s*(>=|>|===|==)\s*(\d+)\b(?!\s*\))", line):
            if int(m.group(3)) >= 3 and not re.search(r"(ms|Date\.now|t0|width|height|status|code|\d{3,})", m.group(1)): bad.append(f"{name}:{i}: {m.group(0)}  ->  derive the number from the data, or explain with // fixed: why it is constant")
SECRET = re.compile(r'PASSWORD|APIKEY|API_KEY|TOKEN|SECRET|Authorization|\b(ARRS|CLIENTS)\b|\["(key|password|token)"\]', re.I)
for path in sorted(glob.glob(os.path.join(HERE, "..", "tests", "*.py"))):
    src = open(path, encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "check" and (len(node.args) >= 3 or any(k.arg == "info" for k in node.keywords)):
            info = node.args[2] if len(node.args) >= 3 else next(k.value for k in node.keywords if k.arg == "info")
            if isinstance(info, ast.Constant): continue   # a sentence written in the test is fine
            text = ast.get_source_segment(src, info) or ""
            if SECRET.search(text): bad.append(f"tests/{os.path.basename(path)}:{node.lineno}: a failed check would print `{text[:60]}`  ->  it can hold a password or key; say what went wrong in words")
print("\n".join(bad) if bad else "bot checks: no fixed counts, no secrets in failure output"); sys.exit(1 if bad else 0)
