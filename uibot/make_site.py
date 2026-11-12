"""Build the GitHub Pages site: a landing page and a static copy of the app page running on recorded data (no server behind it).
Usage: python3 uibot/make_site.py <out dir>      (CI publishes <out dir> to Pages on every push)"""
import json, os, shutil, sys, html
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "site")
LIVE = "https://dedupearr-demo.chrizantoz.com"; REPO = "https://github.com/chrizantoz/dedupearr"
os.makedirs(OUT, exist_ok=True)
for f in ("banner.png", "social-preview.png", "icon.png", "how-it-works.svg", "how-it-works-dark.svg"): shutil.copy(os.path.join(ROOT, "docs", f), OUT)
os.makedirs(os.path.join(OUT, "screenshots"), exist_ok=True)
for f in os.listdir(os.path.join(ROOT, "docs", "screenshots")): shutil.copy(os.path.join(ROOT, "docs", "screenshots", f), os.path.join(OUT, "screenshots", f))

# 1. the static demo: ui.html + the recorded fixtures + a fetch shim (Ignore and Scan now answer like the real server, nothing persists)
fx = {n: json.load(open(os.path.join(ROOT, "uibot", "fixtures", n + ".json"))) for n in ("state", "about")}
fx["state"]["sandbox"] = True; fx["state"]["sandbox_note"] = "This is a static copy of the page on recorded data; nothing runs behind it and nothing you click is kept. The live demo scans a real fake disk."
fx["about"]["health"].append({"level": "ok", "text": "Static demo: the page on recorded answers, published by GitHub Pages."})
shim = """<script>
const __FX=%s;const __ok=(o,s=200)=>({ok:s<400,status:s,json:async()=>JSON.parse(JSON.stringify(o)),text:async()=>JSON.stringify(o)});
window.fetch=async(u,o)=>{u=String(u);if(o&&o.method==='POST'){let b={};try{b=JSON.parse(o.body||'{}')}catch(e){}
 if(u.includes('api/unignore')){const i=__FX.state.items.find(i=>i.path===b.path);if(i){i.ignored=false;i.needs_you=true}return __ok({ok:true,note:'back in Needs you'})}
 if(u.includes('api/ignore')){const i=__FX.state.items.find(i=>i.path===b.path);if(i){i.ignored=true;i.needs_you=false}return __ok({ok:true,note:'hidden from Needs you; it stays in Everything'})}
 if(u.includes('api/scan'))return __ok({ok:true,note:'this static copy cannot scan; the live demo at %s can'});return __ok({error:'not here'},404)}
 if(u.includes('api/state'))return __ok(__FX.state);if(u.includes('api/about'))return __ok(__FX.about);return __ok({},404)};
</script>""" % (json.dumps(fx, separators=(",", ":")), LIVE)
ui = open(os.path.join(ROOT, "ui.html"), encoding="utf-8").read().replace("<body>", "<body>" + shim, 1).replace("<title>Dedupearr</title>", "<title>Dedupearr · static demo</title>")
open(os.path.join(OUT, "demo.html"), "w", encoding="utf-8").write(ui)

# 2. the landing page
verdicts = [("Real copy", "the library file and the download are one file with two names (a hardlink); seeding costs nothing", "good"),
            ("Stored twice", "the library file and the download are two separate files with identical content", "warn"),
            ("Stored twice, header differs", "the same, except the first megabyte: a language tag or title was edited on one copy", "warn"),
            ("Download without a match", "nothing in the library matches this download; phase 2 asks the torrent client", "warn"),
            ("Looks alike", "same size as a library file but different content; not a duplicate", "grey"),
            ("Old recycle-bin file", "in a recycle bin longer than your rule", "grey")]
rows = "".join(f'<tr><td><span class="chip {t}">{html.escape(n)}</span></td><td>{html.escape(w)}</td></tr>' for n, w, t in verdicts)
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dedupearr</title><meta name="description" content="Dedupearr: the truth about your storage. Hardlinks, second copies, leftovers and orphans explained per file, with the evidence.">
<meta property="og:title" content="Dedupearr: the truth about your storage"><meta property="og:image" content="social-preview.png"><link rel="icon" href="icon.png">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600&display=swap">
<style>
:root{{--bg:#0f1419;--panel:#141b22;--line:#263039;--fg:#e6e9ee;--fg2:#b6c0cb;--muted:#95a0ad;--strong:#f3f5f7;--accent:#38bdf8;--accent-bg:#10303f;--accent-fg:#e0f2fe;--good:#86efac;--good-bg:#0f2e1d;--warn:#fde68a;--warn-bg:#3b2f0a;--grey:#cbd5e1;--grey-bg:#1f2937;color-scheme:dark}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 Inter,system-ui,sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:24px max(16px,env(safe-area-inset-right,0px)) 64px max(16px,env(safe-area-inset-left,0px));display:flex;flex-direction:column;gap:40px}}
h1,h2{{font-family:"Space Grotesk",Inter,sans-serif;color:var(--strong);text-wrap:balance;margin:0}}h1{{font-size:clamp(28px,5vw,44px)}}h2{{font-size:24px}}
p{{margin:0;max-width:70ch;color:var(--fg2)}}a{{color:var(--accent)}}
.hero img{{width:100%;height:auto;border-radius:14px;border:1px solid var(--line)}}
.cta{{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px}}.cta a{{display:inline-block;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:500;border:1px solid var(--line);background:var(--panel);color:var(--fg)}}.cta a.primary{{background:var(--accent-bg);border-color:var(--accent);color:var(--accent-fg)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;display:flex;flex-direction:column;gap:8px}}.card b{{color:var(--strong)}}
table{{border-collapse:collapse;width:100%}}td{{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;color:var(--fg2)}}
.chip{{display:inline-block;padding:3px 10px;border-radius:999px;font-size:13px;font-weight:500;white-space:nowrap}}.chip.good{{background:var(--good-bg);color:var(--good)}}.chip.warn{{background:var(--warn-bg);color:var(--warn)}}.chip.grey{{background:var(--grey-bg);color:var(--grey)}}
.shots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}.shots img{{width:100%;height:auto;border-radius:12px;border:1px solid var(--line)}}
pre{{background:#0b0f14;border:1px solid var(--line);border-radius:12px;padding:16px;overflow-x:auto;font-size:13px;color:var(--fg)}}
footer{{color:var(--muted);font-size:14px}}
</style></head><body><main>
<section class="hero"><img src="banner.png" alt="Dedupearr: the truth about your storage">
<h1 style="margin-top:24px">Your download folder is supposed to share its files with your library. Dedupearr tells you whether it does.</h1>
<p>Every night it walks every video file and says what the file <em>is</em>: the real copy, a hardlink that costs nothing, a second copy that wastes space, a download nothing matches, or an old recycle-bin entry. With the evidence, in one plain sentence. The companion to <a href="https://github.com/chrizantoz/auditarr">Auditarr</a>.</p>
<div class="cta"><a class="primary" href="{LIVE}">🧪 Try the live demo</a><a href="demo.html">Static copy of the page</a><a href="{REPO}#-quick-start-docker">Install it</a><a href="{REPO}">Source on GitHub</a></div></section>
<section><h2>What it tells you</h2><p style="margin:8px 0 14px">One of these per file, with the inodes, link counts, sizes and checksums behind it one click away.</p>
<div style="overflow-x:auto"><table>{rows}</table></div></section>
<section><h2>How it works</h2><p style="margin:8px 0 14px">Phase 1 is read-only: it looks and tells. Phase 2 asks the torrent client and the arrs; phase 3 adds the fixes behind a dry run and a 14-day undo.</p>
<picture><source media="(prefers-color-scheme: light)" srcset="how-it-works.svg"><img src="how-it-works-dark.svg" alt="Every night Dedupearr reads the disk, groups names that share one file, compares the rest by checksum and gives each file one verdict with its evidence" style="width:100%;height:auto"></picture></section>
<section><h2>What it looks like</h2><div class="shots" style="margin-top:14px"><img src="screenshots/files-dark.png" alt="Files tab"><img src="screenshots/space-dark.png" alt="Space tab"><img src="screenshots/about-dark.png" alt="About tab"><img src="screenshots/phone-dark.png" alt="Phone layout"></div></section>
<section><h2>Run it</h2><p style="margin:8px 0 14px">One container, one read-only mount that holds both the library and the download folder.</p>
<pre>docker run -d --name dedupearr --restart unless-stopped -p 3092:3092 \\
  -e TZ=America/Winnipeg -v /path/to/appdata/dedupearr:/config -v /path/to/media:/data:ro \\
  ghcr.io/chrizantoz/dedupearr:latest</pre>
<div class="grid"><div class="card"><b>Unraid</b><span>Add Container → paste the template URL from the <a href="{REPO}/blob/main/docs/INSTALL.md">install guide</a>.</span></div>
<div class="card"><b>Docker compose</b><span>A ready file is in <a href="{REPO}/blob/main/docs/docker-compose.yml">docs/docker-compose.yml</a>.</span></div>
<div class="card"><b>Safe by construction</b><span>Read-only mount, no code path that changes a file, verdicts only with evidence. <a href="{REPO}/blob/main/SECURITY.md">Security</a> · <a href="{REPO}/blob/main/docs/TESTING.md">How it is tested</a>.</span></div></div></section>
<footer>GPL-3.0 · <a href="{REPO}/blob/main/docs/USER-GUIDE.md">User guide</a> · <a href="{REPO}/blob/main/FEATURES.md">Features</a> · <a href="{REPO}/blob/main/ROADMAP.md">Roadmap</a> · <a href="{REPO}/blob/main/CHANGELOG.md">Changelog</a> · <a href="https://ko-fi.com/chrizantoz">Ko-fi</a> · <a href="https://github.com/sponsors/chrizantoz">GitHub Sponsors</a></footer>
</main></body></html>"""
open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(page)
print("site written to", OUT, ":", sorted(os.listdir(OUT)))
