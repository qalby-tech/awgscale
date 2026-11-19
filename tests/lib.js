// Shared helpers for the browser tests. Each test file is run by run.sh, which
// starts the stub (tests/stub.py) and a headless Chromium and sets BASE and CDP.
const puppeteer = require(process.env.PUPPETEER || require("path").join(__dirname, "node_modules", "puppeteer-core"));
const BASE = process.env.BASE || "http://127.0.0.1:38787";
const CDP = process.env.CDP || "http://127.0.0.1:39222";
const sleep = ms => new Promise(r => setTimeout(r, ms));
let n = 0, failed = 0;
const errors = [];
function ok(cond, what) { n++; if (!cond) failed++; console.log((cond ? "ok   " : "FAIL ") + what); return !!cond; }
async function ctl(obj) { return (await fetch(BASE + "/__ctl", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj) })).json(); }
async function apiLog() { return (await fetch(BASE + "/__log")).json(); }
// requests that change something (everything but GET, and not the login)
async function changes() { return (await apiLog()).filter(x => x.method !== "GET" && !/login|logout/.test(x.path)); }
async function connect() {
  for (let i = 0; i < 40; i++) { try { return await puppeteer.connect({ browserURL: CDP }); } catch (_) { await sleep(500); } }
  throw new Error("no browser at " + CDP);
}
// a fresh page on a route ("users", "sites", "sites/blocked", "overview", "dns", "servers", "journal", "users/spaces")
async function open(browser, route, opt) {
  const page = await browser.newPage();
  await page.setViewport(Object.assign({ width: 1360, height: 900, deviceScaleFactor: 1 }, (opt && opt.viewport) || {}));
  page.on("pageerror", e => errors.push(String(e).slice(0, 300)));
  page.on("dialog", d => (opt && opt.dismiss ? d.dismiss() : d.accept()));
  await page.goto(BASE + "/#/" + route, { waitUntil: "networkidle0" });
  await page.evaluate(() => { try { localStorage.clear(); } catch (_) { /* no storage */ } });
  await page.reload({ waitUntil: "networkidle0" });
  await sleep(500);
  return page;
}
// the labels of the menu that is open now, and a click on one of them
const menu = page => page.evaluate(() => [...document.querySelectorAll("#menu button")].map(b => b.firstChild ? b.firstChild.textContent : b.textContent));
async function pick(page, label) {
  for (const b of await page.$$("#menu button")) if ((await b.evaluate(e => (e.firstChild ? e.firstChild.textContent : e.textContent))) === label) { await b.click(); await sleep(500); return true; }
  return false;
}
const toasts = page => page.evaluate(() => [...document.querySelectorAll(".toast")].map(t => t.textContent));
async function finish(browser, pages) {
  ok(errors.length === 0, "no script errors on the page: " + JSON.stringify(errors.slice(0, 3)));
  console.log(`\n${n - failed} of ${n} checks passed`);
  for (const p of pages || []) await p.close().catch(() => {});
  await browser.disconnect();
  process.exit(failed ? 1 : 0);
}
function run(fn) { fn().catch(e => { console.error("ERR", String(e && e.stack || e).slice(0, 600)); process.exit(2); }); }
module.exports = { BASE, sleep, ok, ctl, apiLog, changes, connect, open, menu, pick, toasts, finish, run };
