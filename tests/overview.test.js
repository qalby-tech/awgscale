// «Обзор»: the blocks — speed, exits, people by group, sites, filter, the latest of the journal.
const { ok, ctl, changes, connect, open, finish, run } = require("./lib");
run(async () => {
  const browser = await connect();
  const page = await open(browser, "overview");
  // requests counted in the tab itself: other tabs of this file ask on their own
  const asks = (path, p) => (p || page).evaluate(path => performance.getEntriesByType("resource").filter(e => e.name.endsWith(path)).length, path);
  ok((await asks("/api/traffic")) === 1, "the history is asked for once when the page opens");
  ok((await page.$eval("#tOnline", e => e.textContent)) === "5 из 11" && (await page.$eval("#tExits", e => e.textContent)) === "2 из 2", "tiles: 5 of 11 online, 2 of 2 exits");
  ok(!(await page.$("#net")) && !(await page.$("#map")), "the network is drawn in «Сеть», not here");
  // ── the blocks of «Обзор» ─────────────────────────────────────────────────
  const blocks = p => p.evaluate(() => ({
    exits: [...document.querySelectorAll(".exits .exit-row")].map(e => e.querySelector("b").textContent + ":" + e.classList.contains("down") + ":" + e.querySelector(".exit-meta").textContent),
    people: [...document.querySelectorAll(".people-groups li")].map(e => e.innerText.replace(/\n+/g, " ")),
    journal: document.querySelectorAll("#ovJournal .latest li").length,
    speed: document.getElementById("tSpeed").textContent }));
  await ctl({ set: { exits_down: [] } });
  await page.waitForFunction(() => document.getElementById("tExits").textContent === "2 из 2", { timeout: 8000 }).catch(() => {});
  let bl = await blocks(page);
  ok(bl.exits.join("|") === "exit1-nl:false:6 человек · доля 67%|exit2-nl:false:4 человека · доля 33%", "the exits block: each exit, its people and its share of the new ones: " + JSON.stringify(bl.exits));
  ok(bl.people.join("|") === "Дом 3 / 4|Офис 1 / 3|Дача 1 / 2|Без группы 0 / 2", "the people block: online of all, by group, the groups by name: " + JSON.stringify(bl.people));
  ok(bl.journal > 0 && bl.journal <= 5 && /^40 Мбит\/с ↓/.test(bl.speed), "the journal block shows the latest (no more than five), the speed is the state's: " + JSON.stringify(bl));
  await ctl({ set: { exits_down: [3] } });
  await page.waitForFunction(() => document.getElementById("tExits").textContent === "1 из 2", { timeout: 8000 }).catch(() => {});
  bl = await blocks(page);
  ok(bl.exits[1] && bl.exits[1].startsWith("exit2-nl:true") && (await page.$eval("#tExits", e => e.textContent)) === "1 из 2", "an exit that is down is marked in its block: " + JSON.stringify(bl.exits));
  await ctl({ set: { exits_down: [] } });
  const phone = await open(browser, "overview", { viewport: { width: 390, height: 844 } });
  const ph = await phone.evaluate(() => { const nav = document.querySelector(".side").getBoundingClientRect(); return { wide: document.documentElement.scrollWidth, bottom: Math.round(nav.bottom), top: Math.round(nav.top), h: innerHeight }; });
  ok(ph.wide <= 390 && ph.bottom === ph.h && ph.top > ph.h - 90, "phone width: «Обзор» does not scroll sideways, the sections are a bar at the bottom: " + JSON.stringify(ph));
  ok((await changes()).length === 0, "looking at the overview changes nothing on the server");
  await finish(browser, [page, phone]);
});
