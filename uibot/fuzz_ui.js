// Fuzz the page's data: load ui.html once, then feed it hundreds of mangled copies of the real state and about answers
// (missing fields, nulls, wrong types, huge strings, odd unicode) and redraw every tab. No JavaScript error allowed.
// Usage: node fuzz_ui.js <ui.html> <fixtures dir> [iterations=300] [seed=7]     Exit 1 when any mangled state throws.
const fs = require("fs"), path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");
const [,, uiPath, dataDir, nArg, seedArg] = process.argv; const N = +(nArg || 300); let seed = +(seedArg || 7);
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff; const pick = a => a[Math.floor(rnd() * a.length)];
const html = fs.readFileSync(uiPath, "utf8");
const data = {}; for (const n of ["state", "about"]) data[n] = JSON.parse(fs.readFileSync(path.join(dataDir, n + ".json"), "utf8"));
let current = { ...data }; const errors = [];
const vc = new VirtualConsole(); vc.on("jsdomError", e => errors.push(String(e.message || e)));
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://dedupearr.test/", virtualConsole: vc, beforeParse(w) {
  w.fetch = async (url, opts) => {
    const u = String(url); const body = (o, status = 200) => ({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(o)), text: async () => JSON.stringify(o) });
    if (opts && opts.method === "POST") return body({ ok: true, note: "test" });
    if (u.includes("api/state")) return body(current.state);
    if (u.includes("api/about")) return body(current.about);
    return body({}, 404);
  };
  w.setInterval = () => 0; w.HTMLElement.prototype.scrollIntoView = function () {};
} });
const w = dom.window, d = w.document, $ = s => d.querySelector(s), $$ = s => [...d.querySelectorAll(s)];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const BIG = "x".repeat(5000), ODD = "🧪 ünïcödé ‮ reversed <b>&amp;</b> \u0000";
function mangle(obj, log) {
  const o = JSON.parse(JSON.stringify(obj)); let node = o, trail = [];
  for (let i = 0; i < 1 + Math.floor(rnd() * 4); i++) {
    const keys = node && typeof node === "object" ? Object.keys(node) : []; if (!keys.length) break;
    const k = pick(keys); trail.push(k); if (node[k] && typeof node[k] === "object" && rnd() < 0.75) node = node[k]; else break;
  }
  const parent = trail.length > 1 ? trail.slice(0, -1).reduce((a, k) => a[k], o) : o; const key = trail[trail.length - 1]; if (key === undefined) return o;
  const op = pick(["delete", "null", "empty-string", "empty-list", "empty-object", "number", "string", "huge", "odd", "negative", "bool", "truncate"]); const v = parent[key];
  if (op === "delete") delete parent[key]; else if (op === "null") parent[key] = null; else if (op === "empty-string") parent[key] = ""; else if (op === "empty-list") parent[key] = [];
  else if (op === "empty-object") parent[key] = {}; else if (op === "number") parent[key] = 42; else if (op === "string") parent[key] = "nope"; else if (op === "huge") parent[key] = BIG;
  else if (op === "odd") parent[key] = ODD; else if (op === "negative") parent[key] = -1; else if (op === "bool") parent[key] = false; else if (op === "truncate" && Array.isArray(v)) parent[key] = v.slice(0, 1);
  log.push(`${trail.join(".")} <- ${op}`); return o;
}
process.on("unhandledRejection", e => errors.push("unhandled: " + String(e && e.stack ? e.stack.split("\n")[0] : e)));
process.on("uncaughtException", e => errors.push("uncaught: " + String(e && e.stack ? e.stack.split("\n")[0] : e)));
(async () => {
  await sleep(300); const baseErrors = errors.length; const failures = [];
  for (let i = 0; i < N; i++) {
    const log = []; const target = pick(["state", "state", "state", "about"]); let m = data[target];
    for (let k = 0; k < 1 + Math.floor(rnd() * 3); k++) m = mangle(m, log);
    current = { ...data, [target]: m }; const before = errors.length;
    try {
      await w.eval("load()"); await sleep(15);
      for (const t of ["files", "space", "about"]) { $(`nav button[data-tab=${t}]`).click(); await sleep(t === "about" ? 30 : 8); }
      for (const b of $$("#chips button[data-filter]")) { b.click(); await sleep(3); }
    } catch (e) { errors.push(String(e)); }
    if (errors.length > before) failures.push({ target, log, err: errors.slice(before).join(" | ").slice(0, 200) });
    current = { ...data };
  }
  for (const f of failures.slice(0, 12)) console.log(`FAIL ${f.target}: ${f.log.join("; ")}  ->  ${f.err}`);
  console.log(`\n${failures.length} of ${N} mangled states broke the page (${baseErrors} errors before fuzzing)`); process.exit(failures.length ? 1 : 0);
})();
