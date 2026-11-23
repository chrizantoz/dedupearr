// Dedupearr UI bot: loads ui.html in jsdom with recorded API answers (fixtures/*.json), presses every button and checks the outcome.
// Usage: node ui_bot.js ../ui.html fixtures      Exit 1 on any failure. Checks are data-relative: no fixed counts.
const fs = require("fs"), path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");
const [,, uiPath, dataDir] = process.argv;
const html = fs.readFileSync(uiPath, "utf8");
const data = {}; for (const n of ["state", "about"]) data[n] = JSON.parse(fs.readFileSync(path.join(dataDir, n + ".json"), "utf8"));
for (const n of ["settings", "setup"]) { const f = path.join(dataDir, n + ".json"); data[n] = fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, "utf8")) : null; }   // optional: the server's nightly run fetches only state and about
const EDGE = fs.existsSync(path.join(dataDir, "edge.json")) ? JSON.parse(fs.readFileSync(path.join(dataDir, "edge.json"), "utf8")).case : "";   // tidy | empty | huge | down | errors
const posts = []; const errors = [];
const vc = new VirtualConsole(); vc.on("jsdomError", e => errors.push(String(e.message || e)));
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://dedupearr.test/", virtualConsole: vc, beforeParse(w) {
  w.fetch = async (url, opts) => {
    const u = String(url); const body = (o, status = 200) => ({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(o)), text: async () => JSON.stringify(o) });
    if (EDGE === "down") return body({ error: "gateway" }, 503);
    if (opts && opts.method === "POST") {
      posts.push({ url: u, body: opts.body || "" });
      if (u.includes("api/scan")) return body({ ok: true, note: "scan started; the page updates itself" });
      if (u.includes("api/fix")) return body({ ok: true, dry_run: true, kind: "remove", steps: ["Ask Deluge to remove the torrent without deleting its files.", "Move the file into the holding bin (nothing is deleted).", "Keep it for 14 days, then delete it, unless you press Undo."], frees: 1, frees_h: "4.2 MB", note: "Dry run: nothing was changed." });
      if (u.includes("api/restore")) { const id = JSON.parse(opts.body).id; const h = (data.state.holding || {}).items || []; const k = h.findIndex(x => x.id === id); if (k >= 0) h.splice(k, 1); return body({ ok: true, note: "Put back in its old place. The next scan judges it again." }); }
      if (u.includes("api/setup/test")) return body({ ok: true, note: JSON.parse(opts.body).name + " answered (version 4.0.0)." });
      if (u.includes("api/settings")) return body({ ok: true, note: "Saved. The next scan uses it; press Scan now to see the change." });
      if (u.includes("api/setpassword")) return body({ ok: true, note: "Sign-in is on. This browser stays signed in; others are asked." });
      if (u.includes("api/local_login")) return body({ ok: true, note: "saved" });
      if (u.includes("api/unignore")) return body({ ok: true, note: "back in Needs you" });
      if (u.includes("api/ignore")) { const p = JSON.parse(opts.body).path; const it = data.state.items.find(i => i.path === p); if (it) { it.ignored = true; it.needs_you = false; } return body({ ok: true, note: "hidden from Needs you; it stays in Everything" }); }
      return body({ error: "not found" }, 404);
    }
    if (u.includes("api/state")) return body(data.state);
    if (u.includes("api/about")) return body(data.about);
    if (u.includes("api/settings")) return data.settings ? body(data.settings) : body({ error: "not found" }, 404);
    if (u.includes("api/setup")) return data.setup ? body(data.setup) : body({ error: "not found" }, 404);
    if (u.includes("api/actions")) return body({ actions: [{ ts: 1, when: "2026-11-12T03:30", mode: "on", kind: "bin", path: "/data/torrents/tv/x.mkv", detail: "ORPHAN", result: "moved to the holding bin" }, { ts: 0, when: "2026-11-11T03:30", mode: "dryrun", kind: "remove", path: "/data/torrents/tv/y.mkv", detail: "", result: "dry run: would free 1 GB" }], fix_mode: data.state.fix_mode });
    return body({}, 404);
  };
  w.setInterval = () => 0; w.HTMLElement.prototype.scrollIntoView = function () {};
} });
const w = dom.window, d = w.document, $ = s => d.querySelector(s), $$ = s => [...d.querySelectorAll(s)];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = []; const check = (name, cond, info = "") => results.push([!!cond, name, String(info).slice(0, 160)]);
const rawCode = /\b[A-Z]{3,}_[A-Z_]{3,}\b/;
process.on("unhandledRejection", e => errors.push("unhandled: " + String(e && e.stack ? e.stack.split("\n")[0] : e)));
(async () => {
  await sleep(300);
  const finish = () => { for (const [ok, name, info] of results) console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  [" + info + "]")); const bad = results.filter(r => !r[0]).length; console.log(`\n${bad} failure(s) of ${results.length}`); process.exit(bad ? 1 : 0); };
  if (EDGE === "down") {
    check("down: the page says it cannot reach Dedupearr instead of breaking", /cannot reach/.test($("#status").textContent), $("#status").textContent);
    for (const t of ["space", "about"]) { $(`nav button[data-tab=${t}]`).click(); await sleep(60); }
    check("down: About says it cannot load", /cannot load/.test($("#health").textContent), $("#health").textContent);
    check("down: no JavaScript errors", errors.length === 0, errors.join(" | ")); finish();
  }
  const items = data.state.items, needs = items.filter(i => i.needs_you).length;
  // Files page
  check("lead sentence is drawn from the state", $("#lead").textContent.trim() === data.state.lead.replace(/\s+/g, " ").trim(), $("#lead").textContent.slice(0, 100));
  check("tiles exist for every family of verdict", $$("#tiles .tile").length === 5);   // fixed: the page draws five families (fine, stored twice, no match, old bin, unsorted)
  check("default filter is Needs a decision and its count matches the data", $("#chips button[data-filter=needs]").getAttribute("aria-pressed") === "true" && $("#chips button[data-filter=needs]").textContent.includes(String(needs)), $("#chips button[data-filter=needs]").textContent);
  check("rows shown = items that need a decision (the page draws at most 500)", $$("#rows tr[id^=r-]").length === Math.min(500, needs), $$("#rows tr[id^=r-]").length);
  const prose = () => { const c = d.body.cloneNode(true); c.querySelectorAll("details.ev, #settings, #log, script, style").forEach(e => e.remove()); return c.textContent; };   // evidence and the settings dump may name a setting; script text is not prose
  check("no raw verdict code anywhere a person reads", !rawCode.test(prose()), (prose().match(rawCode) || [])[0]);
  check("every row has a plain sentence and an evidence toggle", $$("#rows tr[id^=r-]").every(tr => tr.querySelector("details.ev") && tr.children[2].textContent.trim().length > 20));
  check("every row that needs a decision offers Ignore", $$("#rows tr[id^=r-]").every(tr => tr.querySelector("button[data-ign]")));
  // filters: each chip shows exactly the items it names
  const filters = $$("#chips button[data-filter]").map(b => b.dataset.filter);   // re-query each time: draw() re-renders the chips, a stale node's click goes nowhere
  for (const f of filters) {
    $(`#chips button[data-filter=${f}]`).click(); await sleep(20);
    const shown = $$("#rows tr[id^=r-]").length;
    const want = f === "all" ? items.length : f === "needs" ? needs : f === "fine" ? items.filter(i => !i.needs_you && !i.ignored).length : f === "dups" ? items.filter(i => i.code === "DUPLICATE_COPY" || i.code === "DUPLICATE_HEADER").length : f === "plex" ? items.filter(i => i.code === "NOT_IN_PLEX_FOLDER" || i.code === "NOT_IN_PLEX").length : f === "nomatch" ? items.filter(i => ["DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN"].includes(i.code)).length : items.filter(i => i.code === f).length;
    check(`filter '${f}' shows ${want} rows`, shown === Math.min(want, 500), shown);
  }
  // P2.5: the Not in Plex chip is there only with Plex connected, and counts the files Plex does not show
  const notInPlex = items.filter(i => i.code === "NOT_IN_PLEX_FOLDER" || i.code === "NOT_IN_PLEX"), plexOn = Object.keys(data.state.plex || {}).length > 0 || notInPlex.length > 0, plexChip = $("#chips button[data-filter=plex]");
  check("the Not in Plex chip shows only with Plex connected, and counts the files Plex does not show", plexOn ? !!plexChip && plexChip.textContent.trim().endsWith("· " + notInPlex.length) : !plexChip, plexChip ? plexChip.textContent : "no chip");
  check("a file Plex does not show never has a fix button, and one outside Plex's folders offers Ignore", notInPlex.every(i => !i.fixable && (i.code !== "NOT_IN_PLEX_FOLDER" || i.needs_you || i.ignored)), notInPlex.length);
  // kinds of media (2026-11-12): one button per kind present; each shows exactly that kind; counts follow the filter
  const cats = data.state.categories || [];
  check("one kind button per kind present, plus All kinds (hidden when there is only one kind)", cats.length < 2 ? $("#kinds").hidden : $$("#kinds button[data-kind]").length === cats.length + 1, $$("#kinds button").length);
  check("every row carries its kind", $$("#rows tr[id^=r-] .kind").length === $$("#rows tr[id^=r-]").length);
  check("the kinds add up to every file", cats.reduce((n, c) => n + c.files, 0) === items.length, cats.map(c => c.key + ":" + c.files).join(" "));
  $("#chips button[data-filter=all]").click(); await sleep(20);
  for (const k of (cats.length < 2 ? [] : cats.map(c => c.key))) {
    $(`#kinds button[data-kind=${k}]`).click(); await sleep(20);
    const want = items.filter(i => i.category === k).length, shown = $$("#rows tr[id^=r-]").length, lab = cats.find(c => c.key === k).label;
    check(`kind '${k}' shows its ${want} rows and nothing else`, shown === Math.min(want, 500) && $$("#rows tr[id^=r-] .kind").every(e => e.textContent.includes(lab)), shown);
    check(`kind '${k}' button count matches the data`, $(`#kinds button[data-kind=${k}]`).textContent.trim().endsWith("· " + want), $(`#kinds button[data-kind=${k}]`).textContent);
  }
  if (cats.length >= 2) { $("#kinds button[data-kind=all]").click(); await sleep(20); check("All kinds brings every row back", $$("#rows tr[id^=r-]").length === Math.min(items.length, 500)); }
  $("#chips button[data-filter=needs]").click(); await sleep(20);
  for (const f of $$("#tiles .tile").map(t => t.dataset.filter)) { $(`#tiles .tile[data-filter=${f}]`).click(); await sleep(10); check(`tile '${f}' selects its filter`, $("#chips button[aria-pressed=true]").dataset.filter === f, $("#chips button[aria-pressed=true]").dataset.filter); }
  $("#chips button[data-filter=all]").click(); await sleep(10);
  // search
  const q = $("#q"); q.value = "dune"; q.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(20);
  check("search narrows to matching titles", $$("#rows tr[id^=r-]").length === Math.min(500, items.filter(i => (i.title + " " + i.path + " " + i.label).toLowerCase().includes("dune")).length), $$("#rows tr[id^=r-]").length);
  $("#q").value = ""; $("#q").dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(20);
  // phase 2: client state and the seeding rule on the page
  $("#chips button[data-filter=all]").click(); await sleep(10);
  const withClient = items.filter(i => i.client_text), canGo = items.filter(i => i.removable && i.needs_you);
  if (withClient.length) check("every row with a client state shows it under the sentence", withClient.filter(i => d.getElementById("r-" + i.path.replace(/[^a-z0-9]/gi, "-"))).every(i => d.getElementById("r-" + i.path.replace(/[^a-z0-9]/gi, "-")).textContent.includes(i.client_text)), withClient.length);
  const drawn = items.slice(0, 500).filter(i => i.removable && i.needs_you).length;   // the page draws the first 500 items in the data's order
  check("'can go now' chips = rows the seeding rule frees", $$("#rows .chip").filter(c => c.textContent === "can go now").length === drawn, `${$$("#rows .chip").filter(c => c.textContent === "can go now").length} vs ${drawn}`);
  if (canGo.length) check("the lead says how much can go right now", /can go right now/.test($("#lead").textContent), $("#lead").textContent.slice(0, 160));
  // 2026-11-13: a fixable kind of row without a button says why (still seeding, and when it can go), so a missing button never looks broken
  const waiting = items.slice(0, 500).filter(i => i.wait);
  check("every row still seeding says when it can go, where the button would be", waiting.every(i => { const r = d.getElementById("r-" + i.path.replace(/[^a-z0-9]/gi, "-")); return r && !r.querySelector("button[data-fix]") && r.querySelector(".chip.wait") && r.querySelector(".chip.wait").textContent === i.wait; }), waiting.map(i => i.wait).join(" | "));
  // phase 3: Preview fix shows a plan and changes nothing
  const fixables = items.filter(i => i.fixable);
  const drawnFix = items.slice(0, 500).filter(i => i.fixable && i.path !== ((data.state.fixing || {}).path || "")).length;
  check("'Preview fix' is offered exactly on the fixable rows", $$("#rows button[data-fix]").length === drawnFix, `${$$("#rows button[data-fix]").length} vs ${drawnFix}`);
  if (fixables.length) {
    const fb = $("#rows button[data-fix]:not([data-live])"); const fpath = fb ? fb.dataset.fix : ""; if (fb) { fb.click(); await sleep(80); }
    if (fb) check("Preview fix posts the path and shows the dry-run steps under the row", posts.some(p => p.url.includes("api/fix") && p.body.includes(JSON.stringify(fpath))) && $$("#rows .plan:not([hidden]) li").length > 0 && /nothing changed/i.test($("#rows .plan:not([hidden])").textContent), $("#rows .plan:not([hidden])") ? $("#rows .plan:not([hidden])").textContent.slice(0, 120) : "no plan box");
  }
  // P3.1: the banner follows the fix mode; a live fix needs two presses; the Actions tab shows the holding bin and the log, Put back posts the id
  const mode = data.state.fix_mode || "dryrun"; const live = new Set(data.state.live_kinds || []);
  const au = data.state.auth || {}; check("Sign out shows only with a sign-in, and not when the home network let you in", $("#signout").hidden === !(au.enabled && !au.via), JSON.stringify(au));
  check("the banner says what fixes do in this mode", ({ dryrun: /Dry run is on/, off: /switched off/, on: /Fixes are on/ })[mode].test($("#banner").textContent), $("#banner").textContent);
  const fixing = (data.state.fixing || {}).path || "";
  const liveRows = items.slice(0, 500).filter(i => i.fixable && mode === "on" && live.has(i.fix_kind) && i.path !== fixing).length;
  const relRows = items.slice(0, 500).filter(i => i.fixable && mode === "on" && live.has(i.fix_kind) && i.fix_kind === "relink" && i.path !== fixing).map(i => i.path);
  const relBtns = $$("#rows button[data-live='1']").filter(b => /Make them one file/.test(b.textContent));
  check("a live relink says what it does on the button and in its hint (every byte compared, Put back undoes it)", relBtns.length === relRows.length && relBtns.every(b => relRows.includes(b.dataset.fix) && /byte for byte/.test(b.title) && /Put back/.test(b.title)), `${relBtns.length} vs ${relRows.length}`);
  if (relBtns.length) { const rb = relBtns[0]; rb.click(); await sleep(30); check("the first press on a relink asks to be sure in its own words", /make them one file/.test(rb.textContent) && rb.dataset.armed === "1", rb.textContent); delete rb.dataset.armed; rb.textContent = rb.dataset.was; }
  const busyChips = $$("#rows [data-fixing]");
  check("a fix that is still comparing two files shows its progress on its own row instead of a button", fixing && items.slice(0, 500).some(i => i.path === fixing && i.needs_you) ? busyChips.length === 1 && /comparing both files… \d+%/.test(busyChips[0].textContent) && !busyChips[0].closest("tr").querySelector("button[data-fix]") : busyChips.length === 0, `${busyChips.length} chips, fixing=${fixing}`);
  check("'Move to holding bin' appears exactly on rows whose fix is switched on", $$("#rows button[data-live='1']").length === liveRows, `${$$("#rows button[data-live='1']").length} vs ${liveRows}`);
  const rmRows = items.slice(0, 500).filter(i => i.fixable && mode === "on" && live.has(i.fix_kind) && i.fix_kind === "remove" && i.client && !i.client.usenet && !["NZBGet", "SABnzbd"].includes(i.client.client)).map(i => i.path);
  const rmBtns = $$("#rows button[data-live='1']").filter(b => /Remove torrent/.test(b.textContent));
  check("a live fix that removes a torrent says so on the button and in its hint (files kept, Put back returns the file only)", rmBtns.length === rmRows.length && rmBtns.every(b => rmRows.includes(b.dataset.fix) && /files are kept/.test(b.title) && /not the torrent/.test(b.title)), `${rmBtns.length} vs ${rmRows.length}`);
  const packRows = items.slice(0, 500).filter(i => rmRows.includes(i.path) && i.pack);
  check("a torrent with several files says on its button how many files go, and in its hint how many stay", packRows.every(i => { const b = rmBtns.find(x => x.dataset.fix === i.path); return b && b.textContent.includes(`its ${i.pack.files} files`) && (!i.pack.stay || b.title.includes(`${i.pack.stay} linked to the library stay`)); }), `${packRows.length} pack rows`);
  if (rmBtns.length) { const rb = rmBtns[0]; rb.click(); await sleep(30); check("the first press on a torrent removal asks to be sure in its own words", /remove and move/.test(rb.textContent) && rb.dataset.armed === "1", rb.textContent); delete rb.dataset.armed; rb.textContent = rb.dataset.was; }
  const bulkRows = items.filter(i => i.needs_you).slice(0, 500).filter(i => i.bulk && i.fixable && mode === "on" && live.has(i.fix_kind)).map(i => i.path);
  check("'Move all shown' appears only when two or more plain moves are on the page, and counts them", $("#bulk").hidden === (bulkRows.length < 2) && (bulkRows.length < 2 || ($("#bulk-go").textContent.includes(`Move all ${bulkRows.length} shown`) && +$("#bulk-go").dataset.n === bulkRows.length)), `${$("#bulk").hidden} ${$("#bulk-go").textContent} vs ${bulkRows.length}`);
  if (bulkRows.length >= 2) {
    const bb = $("#bulk-go"); const m0 = posts.filter(p => p.url.includes("api/fix_all")).length; bb.click(); await sleep(30);
    check("the first press of 'Move all' only asks to be sure, nothing is posted", posts.filter(p => p.url.includes("api/fix_all")).length === m0 && /Sure\? Press again to move all/.test(bb.textContent), bb.textContent);
    bb.click(); await sleep(120); const sent = posts.filter(p => p.url.includes("api/fix_all")).pop();
    check("the second press posts exactly the plain moves, never a torrent removal", !!sent && JSON.stringify(JSON.parse(sent.body).paths.slice().sort()) === JSON.stringify(bulkRows.slice().sort()) && !rmRows.some(p => JSON.parse(sent.body).paths.includes(p)), sent && sent.body.slice(0, 200));
    if (rmRows.length) check("the hint says torrent removals stay one press each", /one press each/.test($("#bulk-hint").textContent), $("#bulk-hint").textContent);
  }
  if (liveRows) {
    const lb = $("#rows button[data-live='1']"); const n0 = posts.filter(p => p.url.includes("api/fix")).length; lb.click(); await sleep(30);
    check("the first press of a real move only asks to be sure, nothing is posted", posts.filter(p => p.url.includes("api/fix")).length === n0 && /Sure/.test(lb.textContent) && lb.dataset.armed === "1", lb.textContent);
    lb.click(); await sleep(80); check("the second press posts the fix for that file", posts.filter(p => p.url.includes("api/fix")).length === n0 + 1 && posts[posts.length - 1].body.includes(JSON.stringify(lb.dataset.fix)));
  }
  $("nav button[data-tab=actions]").click(); await sleep(80);
  const held = ((data.state.holding || {}).items || []).length;
  check("Actions tab shows, Files hides", !$("#actions").hidden && $("#files").hidden);
  check("the holding bin lists every held file with Put back, or says it is empty", held ? $$("#hold-rows button[data-restore]").length === held : /nothing in the holding bin/.test($("#hold-rows").textContent), $$("#hold-rows tr").length + " rows");
  check("the actions log shows the recorded actions in plain words", $$("#act-rows tr").length === 2 && /dry run/.test($("#act-rows").textContent) && /done/.test($("#act-rows").textContent), $("#act-rows").textContent.slice(0, 120));
  if (held) {
    const rb = $("#hold-rows button[data-restore]"); const rid = +rb.dataset.restore; rb.click(); await sleep(120);
    check("Put back posts the held file's id and the row leaves the bin", posts.some(p => p.url.includes("api/restore") && JSON.parse(p.body).id === rid) && $$("#hold-rows button[data-restore]").length === held - 1, $$("#hold-rows button[data-restore]").length);
  }
  $("nav button[data-tab=files]").click(); await sleep(20);
  // ignore -> item leaves Needs you
  $("#chips button[data-filter=needs]").click(); await sleep(10);
  if (needs) {
    const ign = $("#rows button[data-ign]"); const ignPath = ign.dataset.ign; ign.click(); await sleep(60);
    check("Ignore posts the file's path", posts.some(p => p.url.includes("api/ignore") && p.body.includes(JSON.stringify(ignPath))));
    check("after Ignore the row leaves Needs a decision", $$("#rows tr[id^=r-]").length === Math.min(500, needs - 1), $$("#rows tr[id^=r-]").length);
    check("a toast told the person what happened", /hidden/.test($("#toast").textContent), $("#toast").textContent);
    $("#chips button[data-filter=all]").click(); await sleep(10);
    check("the ignored row shows an ignored chip and Un-ignore", $$("#rows tr").some(tr => tr.textContent.includes("ignored") && tr.querySelector("button[data-un='1']")));
  } else check("nothing needs a decision: the table says so instead of showing an empty grid", /Nothing here|No scan yet/.test($("#rows").textContent), $("#rows").textContent.slice(0, 80));
  // scan button
  $("#scan-now").click(); await sleep(40);
  check("Scan now posts to api/scan and says so", posts.some(p => p.url.includes("api/scan")) && /scan started/.test($("#toast").textContent), $("#toast").textContent);
  // Space page
  $("nav button[data-tab=space]").click(); await sleep(20);
  check("Space tab shows, Files hides", !$("#space").hidden && $("#files").hidden);
  check("the space bar has five segments that sum to the disk", $$("#bar div").length === 5 && Math.abs($$("#bar div").reduce((a, el) => a + parseFloat(el.style.flexBasis || el.style.flex.split(" ")[2] || "0"), 0) - 100) < 1.5, $$("#bar div").map(el => el.style.flex).join("|"));   // fixed: the bar always has five segments (library, waste, to decide, other, free)
  check("every verdict kind is listed with its count", $$("#by-code tr").length === Object.keys(data.state.labels).length);
  // About page
  $("nav button[data-tab=about]").click(); await sleep(60);
  check("About shows health lines and the version", $$("#health li").length === data.about.health.length && $("#version").textContent.includes(data.about.version));
  check("About settings never show a key or password", !/key|token|password|secret/i.test($("#settings").textContent));
  check("Space lists one row per kind of media", $$("#by-kind tr").length === Math.max(1, (data.state.categories || []).length), $$("#by-kind tr").length);
  check("runs table lists the scans", $$("#runs tr").length === data.about.runs.length);
  check("About lists the recent errors, or says there are none", (data.about.errors || []).length ? $("#errors").textContent.split("\n").length === data.about.errors.length : /No errors/.test($("#errors").textContent), $("#errors").textContent.slice(0, 80));
  // Protect the page: open -> a form that posts the password; on -> the home-network box posts its change; set by the container -> no form
  const A = data.state.auth || {}; const box = $("#authbox");
  if (!A.enabled) {
    $("#pw-user").value = "owner"; $("#pw-new").value = "a long password"; const n0 = posts.length; $("#pw-save").click(); await sleep(80);
    check("open page: Turn sign-in on posts the user and password", /Turn sign-in on/.test(box.textContent + $("#pw-save").textContent) && posts.slice(n0).some(p => p.url.includes("api/setpassword") && JSON.parse(p.body).password === "a long password"), posts.slice(n0).map(p => p.url).join(","));
  } else {
    const cb = $("#lan-nologin");
    check("sign-in on: the home-network box shows its state and the router warning", cb && cb.checked === !!A.local_no_login && /router forwards this port/.test(box.textContent), box.textContent.slice(0, 160));
    if (A.source === "env") check("set by the container: the page offers no password form", !$("#pw-save") && /container/.test(box.textContent));
    else {
      const n0 = posts.length; cb.checked = !cb.checked; cb.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(80);
      check("ticking the home-network box posts the change", posts.slice(n0).some(p => p.url.includes("api/local_login") && JSON.parse(p.body).on === cb.checked), posts.slice(n0).map(p => p.url).join(","));
      const n1 = posts.length; $("#pw-off").click(); await sleep(80);
      check("Turn sign-in off posts off with the current password field", posts.slice(n1).some(p => p.url.includes("api/setpassword") && JSON.parse(p.body).off === true && "current" in JSON.parse(p.body)));
    }
  }
  if (data.state.last_run && !/ok/.test(data.state.last_run.note)) check("a failed last scan shows as a warning in health", $$("#health li.warn").length > 0);
  // P4.1 Setup tab: the shared-mount test, one block per connection, Test asks with what is saved, Save sends only what changed
  $("nav button[data-tab=setup]").click(); await sleep(80);
  check("Setup tab shows, Files hides", !$("#setup").hidden && $("#files").hidden);
  const SU = data.setup, SE = data.settings;
  if (!SU || !SE) check("without Setup answers the tab says it cannot load instead of breaking", /cannot load/.test($("#mount-lead").textContent), $("#mount-lead").textContent);
  else {
    check("the shared-mount test says its answer in a sentence, with each finding as a line", $("#mount-lead").textContent === SU.mount.lead && $$("#mount-lines li").length === SU.mount.lines.length && $$("#mount-lines li.warn").length === SU.mount.lines.filter(l => l.level === "warn").length, $("#mount-lead").textContent.slice(0, 120));
    check("one block per connection, each with Save and Test", $$("#conns .conn").length === SU.connections.length && $$("#conns button[data-saveconn]").length === SU.connections.length && $$("#conns button[data-test]").length === SU.connections.length);
    check("a connection set on the container (or a page nobody may change) has its Save and its fields switched off", SU.connections.every(k => { const box = $$("#conns .conn").find(x => x.dataset.conn === k.name); const off = k.locked || !SU.writable; return box && box.querySelector("button[data-saveconn]").disabled === off && [...box.querySelectorAll("[data-set]")].every(el => el.disabled === (off || SE.items.find(i => i.name === el.dataset.set).locked)); }));
    check("a password box never shows a saved password", $$("#conns input[type=password]").every(el => el.value === ""));
    check("the arrs' Download Clients list is shown row by row", $$("#found-rows tr").length === Math.max(1, (SU.discovered.found || []).length), $$("#found-rows tr").length);
    const tb = $("#conns button[data-test]"); const n0 = posts.length; tb.click(); await sleep(80);
    check("Test posts the app's name and shows the answer beside the button", posts.slice(n0).some(p => p.url.includes("api/setup/test") && JSON.parse(p.body).name === tb.dataset.test) && /answered/.test(tb.parentElement.querySelector(".res").textContent), tb.parentElement.querySelector(".res").textContent);
    const open = SU.connections.find(k => !k.locked && SU.writable);
    if (open) {
      const box = $$("#conns .conn").find(x => x.dataset.conn === open.name); const inp = box.querySelector("input[data-set$='_URL']");
      inp.value = "http://192.168.1.10:1234"; const n1 = posts.length; box.querySelector("button[data-saveconn]").click(); await sleep(80);
      const sent = posts.slice(n1).find(p => p.url.includes("api/settings"));
      check("Save on a connection posts only what changed (its address), never an empty password", !!sent && JSON.stringify(JSON.parse(sent.body).set) === JSON.stringify({ [inp.dataset.set]: "http://192.168.1.10:1234" }), sent && sent.body);
    }
    // P1.7 Settings tab: one row per rule, the container's rules locked, Save changes sends only the changed rule, Use the default resets one
    $("nav button[data-tab=rules]").click(); await sleep(80);
    const rules = SE.items.filter(i => i.group === "rules");
    check("Settings tab shows one row per rule, and a rule set on the container is switched off", !$("#rules").hidden && $$("#rules-form .setrow").length === rules.length && rules.every(i => $(`#rules-form [data-set=${i.name}]`).disabled === (i.locked || !SE.writable)), $$("#rules-form .setrow").length);
    check("'Use the default' appears exactly on rules saved on this page", $$("#rules-form button[data-reset]").length === (SE.writable ? rules.filter(i => i.source === "page" && !i.locked).length : 0));
    check("when the page may not change settings it says why", SE.writable || $("#rules-hint").textContent === SE.why_not, $("#rules-hint").textContent);
    check("the settings only the container sets are listed in words", $$("#rules-fixed li").length === 3 && !rawCode.test($("#rules-fixed").textContent), $("#rules-fixed").textContent);   // fixed: three container-only settings (fixes, folders, holding days)
    const free = rules.find(i => !i.locked && SE.writable && i.kind === "int");
    if (free) {
      const el = $(`#rules-form [data-set=${free.name}]`); el.value = String(+free.value + 1); const n2 = posts.length; $("#rules-save").click(); await sleep(80);
      const sent = posts.slice(n2).find(p => p.url.includes("api/settings"));
      check("Save changes posts only the changed rule and says it is saved", !!sent && JSON.stringify(JSON.parse(sent.body).set) === JSON.stringify({ [free.name]: String(+free.value + 1) }) && /Saved/.test($("#toast").textContent), sent && sent.body);
    }
    const rb = $("#rules-form button[data-reset]");
    if (rb) { const n3 = posts.length; const nm = rb.dataset.reset; rb.click(); await sleep(80); check("Use the default posts a reset of that one rule", posts.slice(n3).some(p => p.url.includes("api/settings") && JSON.stringify(JSON.parse(p.body).reset) === JSON.stringify([nm]))); }
  }
  // press everything else once
  for (const b of $$("button")) { try { b.click(); } catch (e) { errors.push("click " + (b.id || b.className) + ": " + e); } await sleep(5); }
  await sleep(100);
  check("no JavaScript errors during the session", errors.length === 0, errors.join(" | ")); finish();
})();
