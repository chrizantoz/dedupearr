// Dedupearr UI bot: loads ui.html in jsdom with recorded API answers (fixtures/*.json), presses every button and checks the outcome.
// Usage: node ui_bot.js ../ui.html fixtures      Exit 1 on any failure. Checks are data-relative: no fixed counts.
const fs = require("fs"), path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");
const [,, uiPath, dataDir] = process.argv;
const html = fs.readFileSync(uiPath, "utf8");
const data = {}; for (const n of ["state", "about"]) data[n] = JSON.parse(fs.readFileSync(path.join(dataDir, n + ".json"), "utf8"));
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
      if (u.includes("api/unignore")) return body({ ok: true, note: "back in Needs you" });
      if (u.includes("api/ignore")) { const p = JSON.parse(opts.body).path; const it = data.state.items.find(i => i.path === p); if (it) { it.ignored = true; it.needs_you = false; } return body({ ok: true, note: "hidden from Needs you; it stays in Everything" }); }
      return body({ error: "not found" }, 404);
    }
    if (u.includes("api/state")) return body(data.state);
    if (u.includes("api/about")) return body(data.about);
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
    const want = f === "all" ? items.length : f === "needs" ? needs : f === "fine" ? items.filter(i => !i.needs_you && !i.ignored).length : f === "dups" ? items.filter(i => i.code === "DUPLICATE_COPY" || i.code === "DUPLICATE_HEADER").length : f === "nomatch" ? items.filter(i => ["DOWNLOAD_UNMATCHED", "DOWNLOAD_OTHER_RELEASE", "LEFTOVER", "ORPHAN"].includes(i.code)).length : items.filter(i => i.code === f).length;
    check(`filter '${f}' shows ${want} rows`, shown === Math.min(want, 500), shown);
  }
  for (const f of $$("#tiles .tile").map(t => t.dataset.filter)) { $(`#tiles .tile[data-filter=${f}]`).click(); await sleep(10); check(`tile '${f}' selects its filter`, $("#chips button[aria-pressed=true]").dataset.filter === f, $("#chips button[aria-pressed=true]").dataset.filter); }
  $("#chips button[data-filter=all]").click(); await sleep(10);
  // search
  const q = $("#q"); q.value = "dune"; q.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(20);
  check("search narrows to matching titles", $$("#rows tr[id^=r-]").length === Math.min(500, items.filter(i => (i.title + " " + i.path + " " + i.label).toLowerCase().includes("dune")).length), $$("#rows tr[id^=r-]").length);
  $("#q").value = ""; $("#q").dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(20);
  // phase 2: client state and the seeding rule on the page
  $("#chips button[data-filter=all]").click(); await sleep(10);
  const withClient = items.filter(i => i.client_text), canGo = items.filter(i => i.removable && i.needs_you);
  if (withClient.length) check("every row with a client state shows it under the sentence", withClient.filter(i => $(`#r-${i.path.replace(/[^a-z0-9]/gi, "-")}`)).every(i => $(`#r-${i.path.replace(/[^a-z0-9]/gi, "-")}`).textContent.includes(i.client_text)), withClient.length);
  check("'can go now' chips = rows the seeding rule frees", $$("#rows .chip").filter(c => c.textContent === "can go now").length === Math.min(500, canGo.length), `${$$("#rows .chip").filter(c => c.textContent === "can go now").length} vs ${canGo.length}`);
  if (canGo.length) check("the lead says how much can go right now", /can go right now/.test($("#lead").textContent), $("#lead").textContent.slice(0, 160));
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
  check("runs table lists the scans", $$("#runs tr").length === data.about.runs.length);
  check("About lists the recent errors, or says there are none", (data.about.errors || []).length ? $("#errors").textContent.split("\n").length === data.about.errors.length : /No errors/.test($("#errors").textContent), $("#errors").textContent.slice(0, 80));
  if (data.state.last_run && !/ok/.test(data.state.last_run.note)) check("a failed last scan shows as a warning in health", $$("#health li.warn").length > 0);
  // press everything else once
  for (const b of $$("button")) { try { b.click(); } catch (e) { errors.push("click " + (b.id || b.className) + ": " + e); } await sleep(5); }
  await sleep(100);
  check("no JavaScript errors during the session", errors.length === 0, errors.join(" | ")); finish();
})();
