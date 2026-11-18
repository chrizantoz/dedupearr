// Dedupearr browser bot: the page in a real headless Chromium (Playwright), phone and desktop, with screenshots, the tasks a person would try
// (the personas), and no JavaScript error allowed. Second argument: a fixtures folder (served locally with fake POST answers) or the URL of
// a running Dedupearr (LIVE: real GETs; writes are skipped unless ALLOW_WRITES=1).
// Usage: node browser_bot.js ../ui.html fixtures        node browser_bot.js ../ui.html http://host:3092
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const [,, uiPath, dataDir] = process.argv;
const LIVE = /^https?:\/\//.test(dataDir || ""); const WRITES = !LIVE || process.env.ALLOW_WRITES === "1";
const html = LIVE ? "" : fs.readFileSync(uiPath, "utf8");
const fixtures = {}; if (!LIVE) for (const n of ["state", "about", "settings", "setup"]) if (fs.existsSync(path.join(dataDir, n + ".json"))) fixtures[n] = fs.readFileSync(path.join(dataDir, n + ".json"), "utf8");
const ignored = new Set();   // the fake server remembers Ignore, like the real one
const server = http.createServer((req, res) => {
  const u = req.url.split("?")[0]; res.setHeader("Content-Type", "application/json");
  if (req.method === "POST" && u === "/api/fix") return res.end(JSON.stringify({ ok: true, dry_run: true, kind: "remove", steps: ["Move the file into the holding bin (nothing is deleted).", "Keep it for 14 days, then delete it, unless you press Undo."], frees: 1, frees_h: "4.2 MB", note: "Dry run: nothing was changed." }));
  if (req.method === "POST") {
    let raw = ""; req.on("data", c => { raw += c; });
    return req.on("end", () => {
      let b = {}; try { b = JSON.parse(raw || "{}"); } catch (e) { b = {}; }
      if (u === "/api/ignore") ignored.add(b.path); if (u === "/api/unignore") ignored.delete(b.path);
      if (u === "/api/settings") { const st = JSON.parse(fixtures.settings || "{}"); for (const [k, v] of Object.entries(b.set || {})) { const i = (st.items || []).find(x => x.name === k); if (i) { i.value = v; i.source = "page"; } } fixtures.settings = JSON.stringify(st); return res.end(JSON.stringify({ ok: true, note: "Saved. The next scan uses it; press Scan now to see the change." })); }
      if (u === "/api/setup/test") return res.end(JSON.stringify({ ok: true, note: `${b.name} answered (version 4.0.0).` }));
      res.end(JSON.stringify({ ok: true, note: u.includes("unignore") ? "back in Needs you" : u.includes("ignore") ? "hidden from Needs you; it stays in Everything" : "scan started; the page updates itself" }));
    });
  }
  if (u === "/api/state") { const s = JSON.parse(fixtures.state); for (const i of s.items) if (ignored.has(i.path)) { i.ignored = true; i.needs_you = false; } return res.end(JSON.stringify(s)); }
  for (const n of Object.keys(fixtures)) if (u === "/api/" + n) return res.end(fixtures[n]);
  if (u === "/" || u === "/index.html") { res.setHeader("Content-Type", "text/html"); return res.end(html); }
  res.statusCode = 404; res.end("{}");
});
const results = []; const check = (name, cond, info = "") => results.push([!!cond, name, String(info).slice(0, 160)]);
const rawCode = /\b[A-Z]{3,}_[A-Z_]{3,}\b/;
(async () => {
  let base = dataDir; if (!LIVE) { await new Promise(r => server.listen(0, "127.0.0.1", r)); base = `http://127.0.0.1:${server.address().port}/`; }
  const origin = new URL(base).origin;
  const browser = await chromium.launch(); fs.mkdirSync(path.join(__dirname, "screens"), { recursive: true });
  for (const [label, width, height] of [["phone", 400, 860], ["desktop", 1280, 900]]) {
    const page = await browser.newPage({ viewport: { width, height } }); const jsErrors = [];
    page.on("pageerror", e => jsErrors.push(String(e)));
    page.on("console", m => { if (m.type() !== "error") return; const loc = m.location && m.location(); const where = loc && loc.url ? loc.url : ""; if (/Failed to load resource/.test(m.text()) && where && !where.startsWith(origin)) return; jsErrors.push(m.text()); });
    await page.goto(base); await page.waitForSelector("#rows tr", { timeout: 15000 });
    check(`${label}: the page does not scroll sideways`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), await page.evaluate(() => document.documentElement.scrollWidth + " vs " + window.innerWidth));
    check(`${label}: the lead sentence is visible and names a size`, /\d+(\.\d+)? ?(B|KB|MB|GB|TB)/.test(await page.textContent("#lead")) || /No scan yet/.test(await page.textContent("#lead")), await page.textContent("#lead"));
    check(`${label}: no raw verdict code on the page`, !rawCode.test(await page.evaluate(() => { const c = document.body.cloneNode(true); c.querySelectorAll("details.ev,#settings,#log,script,style").forEach(e => e.remove()); return c.textContent; })));
    await page.screenshot({ path: path.join(__dirname, "screens", `files-${label}.png`), fullPage: true });
    try {
      // persona 1: "how much space can I get back?" -> the first screen says it, in words
      const lead = await page.textContent("#lead");
      check(`${label}: persona: the first screen answers 'how much can I get back' without a click`, /wasted|Nothing is wasted|No scan yet|no match/.test(lead), lead);
      // persona 2: "which files are stored twice, and why do you think so?"
      const dups = page.locator("#chips button[data-filter=dups]"); if (await dups.count()) { await dups.click(); await page.waitForTimeout(150); }
      const rows = page.locator("#rows tr[id^=r-]"); const n = await rows.count();
      const stateDups = JSON.parse(await page.evaluate(async () => { const r = await fetch("api/state"); return JSON.stringify((await r.json()).items.filter(i => i.code === "DUPLICATE_COPY" || i.code === "DUPLICATE_HEADER").length); }));
      check(`${label}: persona: the Stored twice filter shows exactly the duplicates`, n === Math.min(stateDups, 500), `${n} vs ${stateDups}`);
      if (n) {
        const first = rows.first(); await first.locator("details.ev summary").click(); await page.waitForTimeout(100);
        const ev = await first.locator("details.ev div").textContent();
        check(`${label}: persona: the evidence names inodes and the other file`, /inode/i.test(ev) && /other file/.test(ev), ev.slice(0, 120));
        check(`${label}: persona: the sentence says how much would be freed`, /\d+(\.\d+)? ?(MB|GB|TB)/.test(await first.locator("td").nth(2).textContent()));
      }
      // persona 2b: "what can I free right now?" -> the Leftovers & orphans filter and the 'can go now' chips agree with the data
      const stateNow = JSON.parse(await page.evaluate(async () => { const r = await fetch("api/state"); const s = await r.json(); return JSON.stringify(s.items.filter(i => i.removable && i.needs_you).length); }));
      await page.locator("#chips button[data-filter=all]").click(); await page.waitForTimeout(150);
      const chips = await page.locator("#rows .chip", { hasText: /^can go now$/ }).count();
      check(`${label}: persona: 'can go now' chips match what the seeding rule frees`, chips === Math.min(stateNow, 500), `${chips} vs ${stateNow}`);
      // persona 2c: "what would the fix do?" -> Preview fix shows steps and says nothing changed (a dry run is safe even on a live server)
      const pf = page.locator("#rows button[data-fix]").first();
      if (await pf.count()) { await pf.click(); await page.waitForSelector("#rows .plan:not([hidden]) li", { timeout: 10000 }).catch(() => {}); check(`${label}: persona: Preview fix lists the steps and says nothing changed`, (await page.locator("#rows .plan:not([hidden]) li").count()) > 0 && /nothing changed/i.test(await page.locator("#rows .plan:not([hidden])").first().textContent()), await page.locator("#rows .plan:not([hidden])").first().textContent().catch(() => "no plan")); }
      // persona 3: "this one is fine, stop showing it" (a write; skipped on a live server unless allowed)
      await page.locator("#chips button[data-filter=needs]").click(); await page.waitForTimeout(150);
      const before = await page.locator("#rows tr[id^=r-]").count(); const ign = page.locator("#rows button[data-ign]").first();
      if (WRITES && await ign.count()) {
        await ign.click(); await page.waitForFunction(() => /hidden|Needs you/.test(document.querySelector("#toast").textContent), null, { timeout: 10000 }).catch(() => {});
        await page.waitForFunction(n => document.querySelectorAll("#rows tr[id^=r-]").length < n, before, { timeout: 10000 }).catch(() => {});   // the page redraws after the answer; on a live server that takes a moment
        check(`${label}: persona: Ignore removes the row from Needs a decision and says so`, (await page.locator("#rows tr[id^=r-]").count()) < before && /hidden/.test(await page.textContent("#toast")), await page.textContent("#toast"));
        if (LIVE) { await page.locator("#chips button[data-filter=all]").click(); await page.waitForTimeout(150); const un = page.locator("#rows button[data-un='1']").first(); if (await un.count()) await un.click(); }   // put it back on a live server
      } else check(`${label}: persona: rows that need a decision offer Ignore`, before === 0 || (await ign.count()) > 0);
    } catch (e) { const lines = String(e).split("\n"); check(`${label}: persona tasks ran without a stuck step`, false, (lines[0] + " " + (lines.find(l => /waiting for|locator\(/.test(l)) || "").trim()).slice(0, 220)); }
    // the other pages, with screenshots
    await page.locator("nav button[data-tab=space]").click(); await page.waitForTimeout(150);
    check(`${label}: Space shows a bar and the verdict table`, (await page.locator("#bar div").count()) > 0 && (await page.locator("#by-code tr").count()) > 0);
    await page.screenshot({ path: path.join(__dirname, "screens", `space-${label}.png`), fullPage: true });
    await page.locator("nav button[data-tab=about]").click(); await page.waitForSelector("#health li", { timeout: 10000 }).catch(() => {});
    check(`${label}: About shows health lines`, (await page.locator("#health li").count()) > 0);
    // persona 4 (P2.3, 2026-11-18): "which download clients does it read, and do they work?" -> About names every client and arr the last scan asked
    const asked = JSON.parse(await page.evaluate(async () => { const r = await fetch("api/state"); return JSON.stringify(Object.keys((await r.json()).clients || {})); }));
    const healthText = await page.textContent("#health");
    check(`${label}: persona: About names every client and arr the last scan asked`, asked.every(n => healthText.includes(n)), asked.filter(n => !healthText.includes(n)).join(", "));
    await page.screenshot({ path: path.join(__dirname, "screens", `about-${label}.png`), fullPage: true });
    // persona 5 (P4.1, 2026-11-18): "can my library and my downloads be one file, and if not, what do I change?" -> the Setup tab answers in a sentence
    await page.locator("nav button[data-tab=setup]").click(); await page.waitForFunction(() => !/Loading/.test(document.querySelector("#mount-lead").textContent), null, { timeout: 10000 }).catch(() => {});
    const ml = await page.textContent("#mount-lead");
    check(`${label}: persona: the Setup tab answers 'can my library and downloads be one file?' in a sentence`, /one mount|separate mounts|different disks|Scan first|no library files|no downloads/.test(ml), ml);
    check(`${label}: persona: every connection on Setup can be tested`, (await page.locator("#conns .conn").count()) === (await page.locator("#conns button[data-test]").count()) && (await page.locator("#conns .conn").count()) > 0);
    const t1 = page.locator("#conns button[data-test]").first();
    if (WRITES || !LIVE) { await t1.click(); await page.waitForFunction(() => /✓|✗/.test((document.querySelector("#conns .res") || {}).textContent || ""), null, { timeout: 15000 }).catch(() => {}); check(`${label}: persona: Test answers beside its button`, /✓|✗/.test(await page.locator("#conns .res").first().textContent()), await page.locator("#conns .res").first().textContent()); }
    await page.screenshot({ path: path.join(__dirname, "screens", `setup-${label}.png`), fullPage: true });
    // persona 6 (P1.7): "keep recycle-bin files 60 days instead of 30" -> the Settings tab changes the rule and says it is saved (a write; skipped on a live server unless allowed)
    await page.locator("nav button[data-tab=rules]").click(); await page.waitForSelector("#rules-form .setrow", { timeout: 10000 }).catch(() => {});
    const rd = page.locator("#rules-form [data-set=RECYCLE_DAYS]");
    if (WRITES && await rd.count() && await rd.isEnabled()) {
      await rd.fill("60"); await page.locator("#rules-save").click(); await page.waitForFunction(() => /Saved/.test(document.querySelector("#toast").textContent), null, { timeout: 10000 }).catch(() => {});
      check(`${label}: persona: a changed rule is saved and the page says so`, /Saved/.test(await page.textContent("#toast")) && (await rd.inputValue()) === "60", await page.textContent("#toast"));
      if (!LIVE) { const st = JSON.parse(fixtures.settings); const i = st.items.find(x => x.name === "RECYCLE_DAYS"); i.value = "30"; i.source = "default"; fixtures.settings = JSON.stringify(st); }
    } else check(`${label}: persona: the recycle-bin rule is on the Settings tab`, (await rd.count()) === 1);
    await page.screenshot({ path: path.join(__dirname, "screens", `settings-${label}.png`), fullPage: true });
    await page.emulateMedia({ colorScheme: "dark" }); await page.goto(base); await page.locator("nav button[data-tab=files]").click(); await page.waitForSelector("#rows tr", { timeout: 15000 });   // dark variant for the README and the banner (the page remembers the last tab, so go back to Files)
    await page.screenshot({ path: path.join(__dirname, "screens", `files-${label}-dark.png`), fullPage: true });
    await page.locator("nav button[data-tab=space]").click(); await page.waitForTimeout(150); await page.screenshot({ path: path.join(__dirname, "screens", `space-${label}-dark.png`), fullPage: true });
    await page.locator("nav button[data-tab=about]").click(); await page.waitForSelector("#health li", { timeout: 10000 }).catch(() => {}); await page.screenshot({ path: path.join(__dirname, "screens", `about-${label}-dark.png`), fullPage: true });
    check(`${label}: no JavaScript errors`, jsErrors.length === 0, jsErrors.join(" | "));
    await page.close();
  }
  await browser.close(); if (!LIVE) server.close();
  for (const [ok, name, info] of results) console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  [" + info + "]"));
  const bad = results.filter(r => !r[0]).length; console.log(`\n${bad} failure(s) of ${results.length}`); process.exit(bad ? 1 : 0);
})().catch(e => { console.error("BROWSER BOT CRASH", e); if (!LIVE) server.close(); process.exit(2); });
