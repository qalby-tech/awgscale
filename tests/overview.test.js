// «Обзор»: tiles and the network map (groups → entry → exits) as a canvas.
const { sleep, ok, ctl, changes, connect, open, toasts, finish, run } = require("./lib");
run(async () => {
  const browser = await connect();
  const page = await open(browser, "overview");
  const view = () => page.evaluate(() => {
    const box = document.getElementById("map"), w = document.getElementById("mapWorld"), b = box.getBoundingClientRect();
    const nodes = [...w.querySelectorAll(".node")].map(e => e.getBoundingClientRect());
    return { groups: [...w.querySelectorAll(".node.group .node-head b")].map(e => e.textContent), users: w.querySelectorAll(".node-user").length, exits: w.querySelectorAll(".node.exit").length,
      links: w.querySelectorAll(".map-links path:not(.flow)").length, tr: w.style.transform,
      inside: nodes.every(r => r.left >= b.left - 1 && r.right <= b.right + 1 && r.top >= b.top - 1 && r.bottom <= b.bottom + 1),
      sel: [...w.querySelectorAll(".sel")].map(e => e.dataset.k || (e.querySelector("[data-k]") || {}).dataset.k),
      side: document.getElementById("mapSide").hidden ? null : document.getElementById("mapSide").innerText.replace(/\n+/g, " | "),
      lit: w.querySelectorAll('.map-links path[stroke="#ff8a3d"]').length, dim: w.querySelectorAll(".map-links path[opacity]").length };
  });
  // picks a thing on the map the way the admin does: by a click on it (a user of a folded group is reached by unfolding groups first); null closes the panel
  const select = async (p, k) => {
    const node = () => p.$('#mapWorld [data-k="' + k + '"]');
    if (k === null) { const x = await p.$("#mapSide .panel-head .icon-btn"); if (x) await x.click(); return; }
    for (const head of await p.$$('#mapWorld .node-head[aria-expanded="false"]')) { if (await node()) break; await head.click(); await sleep(150); }
    const n = await node();
    if (n && (await n.evaluate(e => e.getAttribute("aria-current"))) !== "true") await n.click();
  };
  // the daily bars of the side panel: plain elements, heights as drawn
  const bars = p => p.evaluate(() => {
    const box = document.querySelector("#mapSide .traf-bars"); if (!box) return null;
    const hs = [...box.children].map(e => e.getBoundingClientRect().height), max = Math.max(...hs);
    return { role: box.getAttribute("role"), label: box.getAttribute("aria-label"), n: hs.length, tags: [...new Set([...box.querySelectorAll("*")].map(e => e.tagName))].join(), full: Math.round(max) === box.clientHeight,
      top: hs.map((x, i) => (x === max ? i : -1)).filter(i => i >= 0).join(), today: Math.round(100 * hs[hs.length - 1] / max), now: [...box.children].map((e, i) => (e.classList.contains("now") ? i : -1)).filter(i => i >= 0).join(),
      solid: [...box.children].every(e => getComputedStyle(e).opacity === "1") && getComputedStyle(box.firstChild).backgroundColor, low: Math.min(...hs.filter(x => x > 0)),
      tip: box.lastChild.title, ends: [...document.querySelectorAll("#mapSide .traf-x span")].map(e => e.textContent), wide: box.getBoundingClientRect().right <= document.getElementById("mapSide").getBoundingClientRect().right };
  });
  // requests counted in the tab itself: other tabs of this file ask on their own
  const asks = (path, p) => (p || page).evaluate(path => performance.getEntriesByType("resource").filter(e => e.name.endsWith(path)).length, path);
  const asked = await asks("/api/traffic"), polled = await asks("/api/state");
  ok(asked === 1, "the history is asked for once when the page opens: " + asked);
  ok((await page.$eval("#tOnline", e => e.textContent)) === "5 из 11" && (await page.$eval("#tExits", e => e.textContent)) === "2 из 2", "tiles: 5 of 11 online, 2 of 2 exits");
  let v = await view();
  ok(v.groups.join() === "Дом,Офис,Дача,Без группы" && v.exits === 3 && v.users === 0 && v.links === 4 + 3 && v.inside, "folded map: the four groups, the entry, two exits and «Напрямую», all inside the viewport");
  await (await page.$$("#map .node-head"))[0].click(); await sleep(300);
  v = await view();
  ok(v.users === 4 && v.sel[0] === "g:g00001" && /Дом/.test(v.side) && /Онлайн \| 3 из 4/.test(v.side) && v.lit > 0 && v.dim > 0 && v.inside, "a click on «Дом» unfolds it, lights its links and opens its panel: " + (v.side || "").slice(0, 70));
  ok(/\| С запуска туннеля \| 122 ГБ \| За сутки \| 601 МБ \| За 30 дней \| 20,2 ГБ/.test(v.side) && !/Трафик \|/.test(v.side), "the panel of a group: traffic since the tunnel started (named so, not a bare «Трафик»), over the last day and over 30 days: " + v.side);
  let br = await bars(page);
  const d0 = new Date(Date.now() + 3 * 3600e3 - 29 * 86400e3).toISOString(), first = d0.slice(8, 10) + "." + d0.slice(5, 7);       // 29 days ago, Moscow
  ok(br && br.role === "img" && /^Трафик по дням за 30 дней: всего 20,2 ГБ, больше всего \d\d\.\d\d — 1,2 ГБ, сегодня 501 МБ$/.test(br.label) && br.tags === "I",
    "the group has a chart of 30 days made of plain elements, named for a screen reader: " + (br && br.label + " [" + br.tags + "]"));
  ok(br && br.n === 30 && br.full && br.top === "7,17,27" && br.today === 40 && br.now === "29" && br.wide && /^\d\d\.\d\d — 501 МБ$/.test(br.tip) && br.ends.join() === first + ",макс. 1,2 ГБ,сегодня",
    "one bar per day: the tallest are the full height, today's is 40% of them and the only highlighted one, the ends are dated, the scale is written out: " + JSON.stringify(br));
  ok(br && br.solid === "rgb(154, 163, 174)" && br.low >= 2, "an ordinary day's bar is solid --muted (7:1 on the dark card, not a half-transparent grey) and no bar with traffic is thinner than 2px: " + (br && br.solid + ", " + br.low + "px"));
  await page.click("#mapSide .side-user"); await sleep(300);
  v = await view(); ok(v.sel.some(k => /^u:/.test(k || "")) && /Статус/.test(v.side), "a user picked in the panel is selected: " + (v.side || "").slice(0, 60));
  br = await bars(page);
  ok(/tv_gostinaya/.test(v.side) && /\| С запуска туннеля \| 104 ГБ \| За сутки \| 513 МБ \| За 30 дней \| 17,2 ГБ/.test(v.side) && !/Трафик \|/.test(v.side) && br && br.n === 30 && br.now === "29" && /всего 17,2 ГБ/.test(br.label), "the panel of a user: the same three figures and the user's own 30 bars: " + v.side + " / " + (br && br.label));
  // the chart is not rebuilt by the periodic redraw (a tooltip under a resting pointer would go with it)
  await page.evaluate(() => { document.querySelector("#mapSide .traf-bars").dataset.seen = "1"; });
  const st0 = await asks("/api/state"); await sleep(5500);
  ok((await asks("/api/state")) > st0 && await page.evaluate(() => document.querySelector("#mapSide .traf-bars").dataset.seen === "1"),
    "a state poll leaves the chart's own elements in place");
  // history is loaded, this user has none in it: zeros in the rows, no empty chart
  await select(page, "u:new_phone"); await sleep(300);
  v = await view();
  ok(/new_phone/.test(v.side) && /За сутки \| 0 \| За 30 дней \| 0/.test(v.side) && (await bars(page)) === null && await page.evaluate(() => !document.querySelector("#mapSide .traf-x")), "a user without a byte in 30 days: both rows say 0 and there is no chart: " + v.side);
  await page.click('#map .node.exit[data-k="x:3"]'); await sleep(300);
  v = await view(); ok(v.sel.includes("x:3") && /exit2-nl/.test(v.side) && /Пользователи/.test(v.side), "an exit shows who is on it: " + (v.side || "").slice(0, 80));
  const exitTitles = () => page.evaluate(() => [...document.querySelectorAll("#mapSide .kv")].filter(e => /^Их трафик/.test(e.textContent)).map(e => e.title).join("|"));
  ok(/Пользователи \| \d онлайн из \d \| Их трафик за сутки \| 614 МБ \| Их трафик за 30 дней \| 20,6 ГБ/.test(v.side) && !/\| За сутки|\| За 30 дней/.test(v.side) && (await bars(page)) === null
    && (await exitTitles()) === "Трафик пользователей, которые сейчас на этом выходе|Трафик пользователей, которые сейчас на этом выходе",
    "the panel of an exit: the two rows stand under «Пользователи» and are named as those users' traffic, not the exit's own; no chart: " + v.side.slice(0, 200));
  await sleep(6000); v = await view(); ok(v.sel.includes("x:3") && v.side, "the selection survives the periodic redraw");
  ok((await asks("/api/traffic")) === asked && (await asks("/api/state")) > polled, "the history was asked for when the page opened and not again with the state polls since (" + ((await asks("/api/state")) - polled) + " of them)");
  // a minute later (the page's clock is moved, not waited for) the next poll asks again and the panel shows the new figures
  await ctl({ set: { traffic_x: 2 } });
  await page.evaluate(() => { const real = Date.now.bind(Date); Date.now = () => real() + 61000; });
  await sleep(6500); v = await view();
  ok((await asks("/api/traffic")) === asked + 1 && /Их трафик за сутки \| 1,2 ГБ \| Их трафик за 30 дней \| 41,2 ГБ/.test(v.side), "after a minute the history is asked for once more and the open panel is redrawn with it: " + v.side.slice(0, 160));
  // the request fails a minute later: what was loaded stays, and it is asked again in a minute, not with every poll
  await ctl({ set: { traffic_fail: true } });
  await page.evaluate(() => { const shifted = Date.now.bind(Date); Date.now = () => shifted() + 61000; });
  await sleep(6500); v = await view();
  ok((await asks("/api/traffic")) === asked + 2 && /Их трафик за сутки \| 1,2 ГБ \| Их трафик за 30 дней \| 41,2 ГБ/.test(v.side) && (await toasts(page)).length === 0, "the history request failed: the figures loaded before stay in the panel, nothing is shown about it: " + v.side.slice(0, 160));
  const st1 = await asks("/api/state"); await sleep(10500);
  ok((await asks("/api/traffic")) === asked + 2 && (await asks("/api/state")) >= st1 + 2, "…and the failing request is not repeated with the state polls (" + ((await asks("/api/state")) - st1) + " since)");
  await select(page, "g:g00001"); await sleep(300);
  br = await bars(page);
  ok(br && /всего 40,4 ГБ/.test(br.label) && br.ends[1] === "макс. 2,4 ГБ", "the chart was rebuilt from the history loaded a minute ago: " + (br && br.label));
  await select(page, "x:3"); await sleep(300);
  const t0 = v.tr; await page.click('.map-tools button[aria-label="Крупнее"]'); await sleep(200);
  v = await view(); ok(v.tr !== t0, "«Крупнее» zooms: " + v.tr);
  const b = await (await page.$("#map")).boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + 10); await page.mouse.down(); await page.mouse.move(b.x + b.width / 2 + 70, b.y + 60, { steps: 6 }); await page.mouse.up(); await sleep(200);
  const t1 = (await view()).tr; ok(t1 !== v.tr && (await view()).sel.length > 0, "dragging the canvas moves the map and keeps the selection");
  await page.click('.map-tools button[aria-label="Показать всё"]'); await sleep(300);
  ok((await view()).inside, "«Показать всё» fits everything again");
  await page.mouse.click(b.x + b.width / 2, b.y + 8); await sleep(300);
  v = await view(); ok(v.sel.length === 0 && v.side === null && v.dim === 0, "a click on the empty canvas clears the selection");
  await ctl({ set: { exits_down: [3] } }); await sleep(6500);
  ok(await page.$eval('#map .node.exit[data-k="x:3"]', e => e.classList.contains("down") && /недоступен/.test(e.textContent)) && (await page.$eval("#tExits", e => e.textContent)) === "1 из 2", "an exit that went down is shown as down on the map and in the tile");
  await select(page, "x:3"); await sleep(300); v = await view();
  ok(/недоступен/.test(v.side) && /Пользователи \| \d онлайн из \d \| Их трафик за сутки \| 1,2 ГБ \| Их трафик за 30 дней \| 41,2 ГБ/.test(v.side), "the panel of the exit that is down still counts the users assigned to it, under the same «Их трафик» names: " + v.side.slice(0, 200));
  await select(page, null); await sleep(200);
  await page.setViewport({ width: 390, height: 844 }); await sleep(400); await page.evaluate(() => window.dispatchEvent(new Event("resize"))); await sleep(400);
  ok((await view()).inside && await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "phone width: the map is fitted, the page does not scroll sideways");
  // nothing collected yet: the rows are there with a dash, the chart is not
  await ctl({ set: { traffic_empty: true, traffic_fail: false } });
  const page2 = await open(browser, "overview");
  await (await page2.$$("#map .node-head"))[1].click(); await sleep(300);
  const side2 = await page2.$eval("#mapSide", e => e.innerText.replace(/\n+/g, " | "));
  ok(/Офис/.test(side2) && /С запуска туннеля \| 20,4 ГБ \| За сутки \| — \| За 30 дней \| —/.test(side2) && (await bars(page2)) === null, "without a history the panel shows dashes for the last day and 30 days and no chart: " + side2.slice(0, 120));
  // the very first request for the history fails: as without a history, and one request, not one per poll
  await ctl({ set: { traffic_empty: false, traffic_fail: true } });
  const page3 = await open(browser, "overview");
  await (await page3.$$("#map .node-head"))[1].click(); await sleep(300);
  const side3 = await page3.$eval("#mapSide", e => e.innerText.replace(/\n+/g, " | "));
  ok(/С запуска туннеля \| 20,4 ГБ \| За сутки \| — \| За 30 дней \| —/.test(side3) && (await bars(page3)) === null && (await toasts(page3)).length === 0, "the history could not be loaded: dashes and no chart, as without a history, and no error shown: " + side3.slice(0, 120));
  const st3 = await asks("/api/state", page3); await sleep(11000);
  ok((await asks("/api/traffic", page3)) === 1 && (await asks("/api/state", page3)) >= st3 + 2, "…asked once in " + ((await asks("/api/state", page3)) - st3) + " more state polls, not with each of them");
  await ctl({ set: { traffic_fail: false } });
  await page3.evaluate(() => { const real = Date.now.bind(Date); Date.now = () => real() + 61000; });
  await sleep(6500);
  const side3b = await page3.$eval("#mapSide", e => e.innerText.replace(/\n+/g, " | ")), br3 = await bars(page3);
  ok((await asks("/api/traffic", page3)) === 2 && /За сутки \| 200 МБ \| За 30 дней \| 6,7 ГБ/.test(side3b) && br3 && br3.n === 30, "a minute later it is asked again, and the figures and the chart appear in the open panel: " + side3b.slice(0, 200));
  ok((await changes()).length === 0, "looking at the overview changes nothing on the server");
  await finish(browser, [page, page2, page3]);
});
