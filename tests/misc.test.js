// The remaining pages: navigation and the status line, «Серверы», «Журнал», «DNS без VPN», «Пользователи → Пространства», phone width.
const { BASE, sleep, ok, ctl, changes, connect, open, menu, pick, toasts, finish, run } = require("./lib");
run(async () => {
  const browser = await connect();
  const page = await open(browser, "overview");
  const now = () => Math.floor(Date.now() / 1000);
  // wait for a condition on the page; false (not an exception) when it never comes
  const wait = (p, fn, ms, ...args) => p.waitForFunction(fn, { timeout: ms || 8000, polling: 100 }, ...args).then(() => true, () => false);
  // a fresh load of a route in the same tab
  const go = async (p, route) => { await p.goto(BASE + "/#/" + route); await p.reload({ waitUntil: "networkidle0" }); await sleep(300); };
  const where = p => p.evaluate(() => ({ hash: location.hash, h1: (document.querySelector("#page h1") || {}).textContent, title: document.title,
    cur: [...document.querySelectorAll('#nav a[aria-current="page"]')].map(a => a.textContent), tab: [...document.querySelectorAll('#page .tabs a[aria-current="page"]')].map(a => a.textContent) }));
  const status = p => p.evaluate(() => { const s = document.getElementById("status"); return { text: s.textContent, crit: !!s.querySelector(".dot.crit") }; });
  const sent = async (path, method) => (await changes()).filter(x => x.path === path && (!method || x.method === method));
  const clearToasts = p => p.evaluate(() => document.querySelectorAll(".toast").forEach(t => t.remove()));
  const narrow = p => p.evaluate(() => ({ doc: document.documentElement.scrollWidth, body: document.body.scrollWidth, win: window.innerWidth }));
  const fits = v => v.doc <= v.win && v.body <= v.win;
  const J = v => JSON.stringify(v);

  // ── navigation ─────────────────────────────────────────────────────────────
  const navItems = await page.evaluate(() => [...document.querySelectorAll("#nav a")].map(a => a.textContent + "→" + a.getAttribute("href")));
  ok(navItems.join() === "Обзор→#/overview,Сеть→#/network,Пользователи→#/users,Сайты→#/sites,DNS без VPN→#/dns,Серверы→#/servers,Журнал→#/journal", "the sidebar has the seven sections in order: " + navItems.join());
  for (const [sec, name] of [["network", "Сеть"], ["users", "Пользователи"], ["sites", "Сайты"], ["dns", "DNS без VPN"], ["servers", "Серверы"], ["journal", "Журнал"], ["overview", "Обзор"]]) {
    await page.click(`#nav a[data-sec="${sec}"]`); await sleep(400);
    const w = await where(page);
    ok(w.hash === "#/" + sec && w.h1 === name && w.title === "awgscale — " + name && w.cur.join() === name, `sidebar «${name}» opens its page, sets the title and is the only item marked current: ` + J(w));
  }
  await page.click('#nav a[data-sec="users"]'); await sleep(400);
  await page.evaluate(() => [...document.querySelectorAll("#page .tabs a")].find(a => a.textContent === "Пространства").click()); await sleep(500);
  let w = await where(page);
  ok(w.hash === "#/users/spaces" && w.title === "awgscale — Пользователи" && w.cur.join() === "Пользователи" && w.tab.join() === "Пространства" && await page.$("#spGrid") !== null,
    "the tab «Пространства» opens the spaces under «Пользователи» (title, sidebar and tab marked): " + J(w));
  await page.goBack(); await sleep(500); w = await where(page);
  ok(w.hash === "#/users" && w.tab.join() === "Список" && await page.$("#spGrid") === null, "the browser's Back returns to the list of users: " + J(w));
  await page.click('#nav a[data-sec="dns"]'); await sleep(400);
  await page.evaluate(() => { location.hash = "#/no-such-page"; }); await sleep(500); w = await where(page);
  ok(w.h1 === "Обзор" && w.title === "awgscale — Обзор" && w.cur.join() === "Обзор" && await page.$("#ovJournal") !== null, "an unknown address typed while on another page shows the overview: " + J(w));
  await go(page, "users/whatever"); w = await where(page);
  ok(w.h1 === "Обзор" && w.title === "awgscale — Обзор" && w.cur.join() === "Обзор", "a page loaded with an unknown address shows the overview: " + J(w));
  await page.goto(BASE + "/"); await page.reload({ waitUntil: "networkidle0" }); await sleep(300); w = await where(page);
  ok(w.h1 === "Обзор" && w.cur.join() === "Обзор", "a page loaded with no address at all shows the overview: " + J(w));
  await page.click('#nav a[data-sec="journal"]'); await sleep(400); w = await where(page);
  ok(w.h1 === "Журнал" && w.title === "awgscale — Журнал", "after the fallback the sidebar still works: " + J(w));
  ok((await changes()).length === 0, "walking through the pages changes nothing on the server: " + J((await changes()).map(x => x.method + " " + x.path)));

  // ── «Серверы» and the status line ──────────────────────────────────────────
  await go(page, "servers");
  const srv = () => page.evaluate(() => ({ head: [...document.querySelectorAll(".t-srv thead th")].map(e => e.textContent),
    rows: [...document.querySelectorAll("#srvBody tr")].map(tr => [...tr.children].map(td => td.textContent.replace(/\s+/g, " ").trim())),
    crit: [...document.querySelectorAll("#srvBody tr")].map(tr => !!tr.children[1].querySelector(".st.crit .dot.crit")) }));
  let s = await srv();
  ok(s.head.join() === "Сервер,Статус,Адрес,Доля,Пользователи,Трафик,Связь", "the servers table has its seven columns: " + s.head.join());
  ok(s.rows.length === 3 && s.rows.map(r => r[0].replace(/нагрузка.*/, "")).join() === "Вход,exit1-nl NL,exit2-nl", "one row for the entry and one for each exit, the country beside an exit's name when it is known: " + J(s.rows.map(r => r[0])));
  ok(/нагрузка 0,30 · память 45% · диск 41%/.test(s.rows[0][0]) && s.rows[0].slice(1).join("|") === "работает|порт 58728|—|11|↓ 40 Мбит/с ↑ 1,5 Мбит/с|—", "the entry row: load, memory, disk, «работает», its port, all 11 users and the total traffic: " + J(s.rows[0]));
  ok(s.rows[1].slice(1).join("|") === "работает|198.51.100.38|67%|6|↓ 3,6 Мбит/с ↑ 500 Кбит/с|только что", "exit1-nl: working, address without the port, share 2 of 3, its 6 users, its traffic, last contact: " + J(s.rows[1]));
  ok(s.rows[2].slice(1).join("|") === "работает|198.51.100.163|33%|4|↓ 37 Мбит/с ↑ 960 Кбит/с|только что", "exit2-nl: working, address, share 1 of 3, its 4 users, its traffic: " + J(s.rows[2]));
  ok(s.crit.join() === "false,false,false", "no server is marked red while all work: " + s.crit.join());
  ok(/^exit2-nl\s*нагрузка 0,05 · память 25% · диск 26%$/.test(s.rows[2][0]) && s.rows[1][0] === "exit1-nl NL", "an exit that reports about itself shows load, memory and disk like the entry; one that does not shows only its name: " + J([s.rows[1][0], s.rows[2][0]]));
  // the shares are percents, edited together
  await page.click('#srvBody [data-k="w:1"]'); await sleep(250);
  const shares = () => page.evaluate(() => ({ vals: [...document.querySelectorAll("#exitShares input")].map(i => i.value), names: [...document.querySelectorAll("#exitShares .share-row span:first-child")].map(x => x.textContent),
    sum: (document.querySelector("#exitShares .hint") || {}).textContent, off: (document.querySelector("#exitShares button[type=submit]") || {}).disabled }));
  let sh = await shares();
  ok(sh.names.join() === "exit1-nl,exit2-nl" && sh.vals.join() === "67,33" && sh.sum === "в сумме 100%" && !sh.off, "the share opens an editor with a percent for every exit, adding up to 100: " + J(sh));
  const setPct = async (i, v) => { const el = (await page.$$("#exitShares input"))[i]; await el.click({ clickCount: 3 }); await el.type(String(v)); await sleep(150); };
  await setPct(0, 75); sh = await shares();
  ok(sh.vals.join() === "75,25" && !sh.off, "with two exits the other one follows by itself: " + J(sh.vals));
  await page.click("#exitShares button[type=submit]");
  const sawShare = await wait(page, () => /75%/.test(document.querySelectorAll("#srvBody tr")[1].textContent) && /25%/.test(document.querySelectorAll("#srvBody tr")[2].textContent), 8000);
  let ws = await sent("/api/exits/weights", "POST");
  ok(ws.length === 1 && J(ws[0].body) === J({ weights: { 1: 75, 3: 25 }, base: { 1: 2, 3: 1 } }) && sawShare && await page.$eval("#exitShares", e => e.hidden),
    "saving sends the percents of all exits with what the page showed; the rows show them and the editor closes: " + J(ws.map(x => x.body)));
  await page.click('#srvBody [data-k="w:1"]'); await sleep(250);
  await setPct(0, 67); await setPct(1, 33);
  await page.click("#exitShares button[type=submit]");
  await wait(page, () => /67%/.test(document.querySelectorAll("#srvBody tr")[1].textContent), 8000);
  // the exits as servers: actions of a row, and a new exit
  await page.click('#srvBody button[aria-label="Действия с выходом exit1-nl"]'); await sleep(250);
  let em = await menu(page); await page.keyboard.press("Escape"); await sleep(150);
  ok(em.join() === "Переименовать,Страна,Обновить,Обновить и перезагрузить,Удалить", "an exit made by the panel offers rename, its country, update, update with a reboot, remove: " + em.join());
  await page.click('#srvBody button[aria-label="Действия с выходом exit2-nl"]'); await sleep(250);
  em = await menu(page); await page.keyboard.press("Escape"); await sleep(150);
  ok(em.join() === "Переименовать,Страна,Удалить", "an exit set up by hand offers no update (the panel does not log into it), but its country: " + em.join());
  // the country of an exit: shown beside its name, changed like the name (a question with the code in it)
  ok(await page.$eval('#srvBody [data-k="c:1"]', e => e.title === "Нидерланды") && !(await page.$('#srvBody [data-k="c:3"]')), "the country's name is on hover; an exit whose country is unknown shows none");
  // the question is answered by the page's own prompt, replaced here (the page's other questions are accepted by the harness)
  const answer = reply => page.evaluate(text => { window.__asked = window.__asked || []; window.prompt = (message, value) => { window.__asked.push([message, value]); return text; }; }, reply);
  await answer("DE ");
  await page.click('#srvBody button[aria-label="Действия с выходом exit1-nl"]'); await sleep(250); await pick(page, "Страна");
  const sawCountry = await wait(page, () => /exit1-nl\s*DE/.test(document.querySelectorAll("#srvBody tr")[1].children[0].textContent), 8000);
  let asked = await page.evaluate(() => window.__asked);
  let cs = await sent("/api/exits/country", "POST");
  ok(sawCountry && asked.length === 1 && asked[0][1] === "nl" && /код из двух латинских букв/.test(asked[0][0]) && cs.length === 1 && J(cs[0].body) === J({ index: 1, country: "de" }),
    "«Страна» asks with the current code filled in, sends it in lower case and the row shows the new one: " + J([asked, cs.map(x => x.body)]));
  await answer("auto");
  await page.click('#srvBody button[aria-label="Действия с выходом exit2-nl"]'); await sleep(250); await pick(page, "Страна");
  const sawAuto = await wait(page, () => /exit2-nl\s*NL/.test(document.querySelectorAll("#srvBody tr")[2].children[0].textContent), 8000);
  asked = await page.evaluate(() => window.__asked);
  cs = await sent("/api/exits/country", "POST");
  ok(sawAuto && asked[1][1] === "auto" && cs.length === 2 && J(cs[1].body) === J({ index: 3, country: "auto" }), "an exit without a country is offered «auto» (found again by its address); the found one is shown: " + J([asked[1], cs.map(x => x.body)]));
  await answer("auto");
  await page.click('#srvBody button[aria-label="Действия с выходом exit1-nl"]'); await sleep(250); await pick(page, "Страна");
  await sleep(600);
  asked = await page.evaluate(() => window.__asked);
  cs = await sent("/api/exits/country", "POST");
  const keptRow = await page.evaluate(() => document.querySelectorAll("#srvBody tr")[1].children[0].textContent);
  ok(asked[2][1] === "de" && cs.length === 3 && J(cs[2].body) === J({ index: 1, country: "auto" }) && /exit1-nl\s*DE/.test(keptRow),
    "«auto» that finds nothing by the address keeps the country the exit had (DE stays, not the found NL of the other exit): " + J([asked[2], keptRow]));
  await answer("");
  await page.click('#srvBody button[aria-label="Действия с выходом exit1-nl"]'); await sleep(250); await pick(page, "Страна"); await sleep(300);
  ok((await sent("/api/exits/country", "POST")).length === 3, "an empty answer sends nothing");
  ok(await page.$eval("#exitJob", e => e.hidden) && !(await page.$("#exitAddr")), "no job is shown while there is none, and the form of a new exit is closed");
  await page.click("#exitAddBtn"); await sleep(200);
  await page.type("#exitAddr", "45.10.20.9"); await page.type("#exitName", "exit3-de"); await page.type("#exitPw", "r00t-pw"); { const el = await page.$("#exitShare"); await el.click({ clickCount: 3 }); await el.type("20"); }
  await page.click("#exitForm button[type=submit]");
  const sawJob = await wait(page, () => { const j = document.getElementById("exitJob"); return j && !j.hidden && /Подключение выхода exit3-de — идёт/.test(j.textContent); }, 8000);
  const add = await sent("/api/exits/add", "POST");
  ok(add.length === 1 && J(add[0].body) === J({ address: "45.10.20.9", name: "exit3-de", weight: 20, password: "r00t-pw" }), "the form sends the address, the name, the share and the password once: " + J(add.map(x => Object.keys(x.body))));
  const jobText = await page.$eval("#exitJob", e => [...e.querySelectorAll("li")].map(li => li.className + ":" + li.textContent).join("|"));
  ok(sawJob && jobText === "done:✓Вход на сервер|run:…Проверка и установка пакетов|wait:·Туннель", "the job shows its steps: done, running, waiting: " + jobText);
  ok(!(await page.$("#exitPw")) && !(await page.content()).includes("r00t-pw"), "after sending, the form is closed and the password is nowhere in the page");
  let st = await status(page);
  ok(st.text === "Всё работает" && !st.crit, "the status line says «Всё работает» with a calm dot: " + J(st));
  await ctl({ set: { exits_down: [3] } });
  const sawDown = await wait(page, () => /недоступен/.test(document.querySelectorAll("#srvBody tr")[2].textContent), 8000);
  s = await srv();
  ok(s.rows[2][0] === "exit2-nl NL", "an exit that is down shows no figures about itself (its country stays): " + J(s.rows[2][0]));
  ok(sawDown && s.rows[2][1] === "недоступен" && s.crit[2] && s.rows[2][6] === "15 мин назад" && s.rows[2][5] === "↓ 0 Кбит/с ↑ 0 Кбит/с", "an exit that went down shows «недоступен» in red after the next poll, with no traffic and an old last contact: " + J(s.rows[2]));
  ok(s.rows[1][1] === "работает" && !s.crit[1] && s.rows[0][1] === "работает", "the other exit and the entry stay «работает»: " + J([s.rows[0][1], s.rows[1][1]]));
  st = await status(page);
  ok(st.text === "Есть проблемы" && st.crit, "the status line reports a problem with a red dot while an exit is down: " + J(st));
  await page.click('#nav a[data-sec="dns"]'); await sleep(400); st = await status(page);
  ok(st.text === "Есть проблемы" && st.crit, "the problem stays in the status line on another page: " + J(st));
  await page.click('#nav a[data-sec="servers"]'); await sleep(300);
  ok((await srv()).rows[2][1] === "недоступен", "coming back to «Серверы» shows the exit as down at once, without waiting for a poll: " + J((await srv()).rows[2]));
  await ctl({ set: { exits_down: [1, 3] } });
  await wait(page, () => /недоступен/.test(document.querySelectorAll("#srvBody tr")[1].textContent), 8000);
  s = await srv(); st = await status(page);
  ok(s.rows.length === 3 && s.rows[1][1] === "недоступен" && s.rows[2][1] === "недоступен" && s.rows[0][1] === "работает" && st.text === "Есть проблемы", "with both exits down both rows say so, the entry row stays: " + J(s.rows.map(r => r[1])) + " " + st.text);
  await ctl({ set: { exits_down: [] } });
  const sawUp = await wait(page, () => document.getElementById("status").textContent === "Всё работает", 8000);
  s = await srv();
  ok(sawUp && s.rows[2][1] === "работает" && !s.crit[2] && !(await status(page)).crit, "when the exits are back the rows and the status line recover by themselves: " + J(s.rows.map(r => r[1])));
  ok((await changes()).filter(x => !x.path.startsWith("/api/exits/")).length === 0, "looking at the servers changes nothing on the server (apart from the shares set above)");

  // ── «Журнал» ───────────────────────────────────────────────────────────────
  await ctl({ reset: true }); await ctl({ set: { journal: [] } });
  await go(page, "journal");
  const jr = () => page.evaluate(() => [...document.querySelectorAll("#jrBody tr")].map(tr => ({ cells: [...tr.children].map(td => td.textContent), bad: !!tr.querySelector("td.c-bad"), dot: !!tr.querySelector("td.c-bad .dot.crit"), tags: tr.querySelectorAll("b").length })));
  let j = await jr();
  ok(j.length === 1 && j[0].cells.join() === "Изменений пока не было", "an empty journal says so: " + J(j));
  const t0 = now();
  await ctl({ set: { journal: [
    { t: t0 - 4 * 86400, text: "Старое событие", bad: false, kind: "change", who: "system" },
    { t: t0 - 7200, text: "Создан пользователь new_phone", bad: false, kind: "change", who: "panel" },
    { t: t0 - 3600, text: "Выход exit2-nl недоступен", bad: true, kind: "exit", who: "system" },
    { t: t0 - 1800, text: "Приостановлен: guest_1 <b>x</b>", bad: false, kind: "change", who: "bot" },
    { t: t0 - 600, text: "Запись без автора", bad: false, kind: "change" }] } });
  const sawJournal = await wait(page, () => document.querySelectorAll("#jrBody tr").length === 5, 22000);
  j = await jr();
  ok(sawJournal, "the journal shows new entries by itself, without a reload: " + j.length + " rows");
  ok(j.map(r => r.cells[2]).join("|") === "Запись без автора|Приостановлен: guest_1 <b>x</b>|Выход exit2-nl недоступен|Создан пользователь new_phone|Старое событие", "rows go newest first, each with its text: " + J(j.map(r => r.cells[2])));
  ok(j.map(r => r.cells[1]).join("|") === "|Бот|Система|Панель|Система", "who made the change: Панель / Бот / Система, empty when unknown: " + J(j.map(r => r.cells[1])));
  const hm = await page.evaluate(ts => ts.map(t => { const d = new Date(t * 1000); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); }), [t0 - 600, t0 - 1800, t0 - 3600, t0 - 7200]);
  ok(j.slice(0, 4).every((r, i) => new RegExp("^(сегодня|вчера), " + hm[i] + "$").test(r.cells[0])), "recent rows show the day and the time of the entry: " + J(j.slice(0, 4).map(r => r.cells[0])) + " expected times " + hm.join());
  ok(/^\d{1,2} (января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)$/.test(j[4] ? j[4].cells[0] : ""), "an entry of four days ago shows its date: " + (j[4] && j[4].cells[0]));
  ok(j.map(r => r.bad && r.dot).join() === "false,false,true,false,false", "only the bad entry is marked (red text and a red dot): " + j.map(r => r.bad && r.dot).join());
  ok(j.every(r => r.tags === 0), "an entry's text is shown as text, not as markup");
  const made = await (await fetch(BASE + "/api/clients", { method: "POST", headers: { "Content-Type": "application/json" }, body: J({ name: "journal_probe" }) })).json();
  const sawNew = await wait(page, () => { const r = document.querySelector("#jrBody tr"); return !!r && /Создан пользователь journal_probe/.test(r.textContent); }, 22000);
  j = await jr();
  ok(made.ok && sawNew && j.length === 6 && j[0].cells[1] === "Панель" && /^сегодня|^вчера/.test(j[0].cells[0]) && j[1].cells[2] === "Запись без автора", "a change made elsewhere appears at the top of the journal by itself: " + J(j[0]));

  // ── «DNS без VPN» ──────────────────────────────────────────────────────────
  await ctl({ reset: true });
  const dev = (name, token, last, queries, admitted) => ({ name, token, created: now() - 86400, last: last ? now() - last : 0, ip: last ? "203.0.113.9" : null, queries, admitted });
  const seed = [dev("tv_kitchen", "k3x9q2w8e7r6AbCdEfGhIjKl", 600, 1200, true), dev("old_tablet", "Zz11zz22zz33zz44zz55zz66", 7200, 15234, false), dev("spare_box", "Qq11qq22qq33qq44qq55qq66", 0, 0, false)];
  await ctl({ set: { dns_devices: seed } });
  await go(page, "dns");
  const dns = () => page.evaluate(() => ({ rows: [...document.querySelectorAll("#dnsBody tr")].map(tr => tr.children.length < 4 ? { empty: tr.textContent } : { name: tr.querySelector(".uname").textContent, st: tr.children[1].textContent, off: !!tr.children[1].querySelector(".st.off"),
      q: tr.children[2].textContent.replace(/\s/g, " "), sel: tr.classList.contains("sel"), pressed: tr.querySelector("button.btn").getAttribute("aria-pressed") }),
    panel: document.getElementById("dnsPanel").hidden ? null : { name: document.querySelector("#dnsPanel h2").textContent, url: document.querySelector("#dnsPanel input").value },
    field: document.getElementById("dnsName").value, focus: document.activeElement && document.activeElement.id, addOff: document.getElementById("dnsAdd").disabled }));
  const typeName = async text => { await page.click("#dnsName"); await page.$eval("#dnsName", e => e.select()); await page.keyboard.press("Backspace"); if (text) await page.keyboard.type(text); };
  let d = await dns();
  ok(d.rows.map(r => r.name).join() === "tv_kitchen,old_tablet,spare_box" && d.panel === null, "the list shows every device that has an address, no address panel yet: " + J(d.rows.map(r => r.name)));
  ok(d.rows[0].st === "10 мин назад" && !d.rows[0].off && d.rows[1].st === "2 ч назад" && d.rows[1].off && d.rows[2].st === "не подключалось" && d.rows[2].off, "status: an admitted device is live, a lapsed one is grey, a new one «не подключалось»: " + J(d.rows.map(r => [r.st, r.off])));
  ok(d.rows.map(r => r.q).join("|") === "1 200|15 234|—", "the number of requests of each device: " + J(d.rows.map(r => r.q)));
  await page.click('#dnsBody button[data-k="tv_kitchen:addr"]'); await sleep(300); d = await dns();
  ok(d.panel && d.panel.name === "tv_kitchen" && d.panel.url === BASE + "/dns-query/k3x9q2w8e7r6AbCdEfGhIjKl" && d.rows[0].sel && d.rows[0].pressed === "true" && d.rows[1].pressed === "false", "«Адрес» opens the panel with the device's own DNS address and marks its row: " + J(d.panel));
  await page.click('#dnsBody button[data-k="old_tablet:addr"]'); await sleep(300); d = await dns();
  ok(d.panel && d.panel.name === "old_tablet" && d.panel.url === BASE + "/dns-query/Zz11zz22zz33zz44zz55zz66" && d.rows[1].sel && !d.rows[0].sel, "«Адрес» of another device switches the panel to it: " + J(d.panel));
  await page.click('#dnsBody button[data-k="old_tablet:addr"]'); await sleep(300); d = await dns();
  ok(d.panel === null && d.rows.every(r => !r.sel && r.pressed === "false"), "a second press of «Адрес» closes the panel: " + J(d.panel));
  await page.click('#dnsBody button[data-k="tv_kitchen:addr"]'); await sleep(200); await page.click('#dnsPanel button[aria-label="Закрыть"]'); await sleep(300); d = await dns();
  ok(d.panel === null && d.rows.every(r => !r.sel), "«Закрыть» closes the panel");
  for (const [bad, why] of [["имя", "Cyrillic"], ["-box", "a leading dash"], ["my box", "a space"], ["a.b", "a dot"], ["", "empty"]]) {
    await clearToasts(page); await typeName(bad); await page.keyboard.press("Enter"); await sleep(400);
    const t = await toasts(page); d = await dns();
    ok(t.length === 1 && /^Имя: латиница, цифры, _ и -, до 32 символов$/.test(t[0]) && (await sent("/api/dns")).length === 0 && d.field === bad && d.focus === "dnsName" && d.rows.length === 3,
      `a name with ${why} is refused with an explanation, nothing is sent, the field keeps the text and the focus: ` + J({ t, field: d.field, focus: d.focus }));
  }
  await typeName("x".repeat(40)); d = await dns();
  ok(d.field.length === 32, "the field does not take more than 32 characters: " + d.field.length);
  await clearToasts(page); await typeName("  tv_spalnya "); await page.keyboard.press("Enter");
  const sawDev = await wait(page, () => [...document.querySelectorAll("#dnsBody .uname")].some(e => e.textContent === "tv_spalnya"), 5000);
  await sleep(300); d = await dns(); let p = await sent("/api/dns", "POST");
  ok(sawDev && p.length === 1 && J(p[0].body) === '{"name":"tv_spalnya"}' && d.rows.length === 4 && d.rows[3].name === "tv_spalnya" && d.rows[3].st === "не подключалось", "a valid name (spaces around it trimmed) is sent once and the device appears in the list: " + J(p.map(x => x.body)) + " " + J(d.rows.map(r => r.name)));
  ok(d.field === "" && !d.addOff && (await toasts(page)).length === 0, "after that the field is empty, the button is active again and there is no error: " + J({ field: d.field, addOff: d.addOff, t: await toasts(page) }));
  ok(d.panel && d.panel.name === "tv_spalnya" && d.panel.url.startsWith(BASE + "/dns-query/") && d.rows[3].sel, "the address panel of the new device opens at once: " + J(d.panel));
  // (the code inside that address is not checked: the stub issues it under another field name — see stub_gaps)
  await clearToasts(page); await typeName("tv_kitchen"); await page.click("#dnsAdd"); await sleep(600);
  let t = await toasts(page); d = await dns();
  ok(t.join() === "такое имя уже есть" && d.rows.length === 4 && d.field === "tv_kitchen" && !d.addOff, "a name that is taken: the server's refusal is shown, the list is unchanged, the field keeps the name: " + J({ t, field: d.field, n: d.rows.length }));
  await ctl({ fail: "/api/dns" }); await clearToasts(page); await typeName("box_2"); await page.keyboard.press("Enter"); await sleep(600);
  t = await toasts(page); d = await dns();
  ok(/сервер отказал/.test(t.join()) && d.rows.length === 4 && d.field === "box_2" && !d.addOff, "a refusal by the server is shown, the name stays in the field for another try: " + J({ t, field: d.field }));
  await page.keyboard.press("Enter"); const sawBox = await wait(page, () => document.querySelectorAll("#dnsBody tr").length === 5, 5000);
  ok(sawBox && (await dns()).rows[4].name === "box_2", "the second try of the same name goes through: " + J((await dns()).rows.map(r => r.name)));

  // the periodic refresh must not take the focus (or the text) out of the field being typed in
  await page.click('#dnsPanel button[aria-label="Закрыть"]').catch(() => {});
  await typeName("half_typ");
  const cur = (await (await fetch(BASE + "/api/dns")).json()).devices;
  await ctl({ set: { dns_devices: cur.concat([dev("from_bot", "Bb11bb22bb33bb44bb55bb66", 0, 0, false)]) } });
  const sawBot = await wait(page, () => [...document.querySelectorAll("#dnsBody .uname")].some(e => e.textContent === "from_bot"), 40000);
  d = await dns();
  ok(sawBot, "a device added elsewhere appears in the list by itself: " + J(d.rows.map(r => r.name)));
  ok(d.focus === "dnsName" && d.field === "half_typ", "while the list was redrawn the field kept the focus and the half-typed name: " + J({ focus: d.focus, field: d.field }));
  await page.keyboard.type("ed"); await sleep(5600); d = await dns();
  ok(d.focus === "dnsName" && d.field === "half_typed", "typing goes on into the same field, also across the next 5-second poll: " + J({ focus: d.focus, field: d.field }));
  await typeName("");

  // revoking
  await clearToasts(page);
  await page.click('#dnsBody button[data-k="old_tablet:addr"]'); await sleep(200);
  await page.click('#dnsBody button[aria-label="Действия с устройством old_tablet"]'); await sleep(300);
  const dm = await menu(page);
  ok(dm.join() === "Отозвать", "the row menu of a device offers «Отозвать»: " + dm.join());
  await pick(page, "Отозвать");
  const sawGone = await wait(page, () => ![...document.querySelectorAll("#dnsBody .uname")].some(e => e.textContent === "old_tablet"), 5000);
  d = await dns(); p = await sent("/api/dns/old_tablet", "DELETE"); t = await toasts(page);
  ok(sawGone && p.length === 1 && t.join() === "Адрес отозван" && d.rows.length === 5 && (await sent("/api/dns/tv_kitchen")).length === 0, "«Отозвать» (confirmed) removes that one device and says «Адрес отозван»: " + J({ t, rows: d.rows.map(r => r.name) }));
  ok(d.panel === null, "the address panel of the revoked device closes: " + J(d.panel));
  for (const n of ["tv_kitchen", "spare_box", "tv_spalnya", "box_2", "from_bot"]) {
    await page.click(`#dnsBody button[aria-label="Действия с устройством ${n}"]`); await sleep(200); await pick(page, "Отозвать");
    await wait(page, name => ![...document.querySelectorAll("#dnsBody .uname")].some(e => e.textContent === name), 5000, n);
  }
  d = await dns();
  ok(d.rows.length === 1 && d.rows[0].empty === "Устройств пока нет" && (await (await fetch(BASE + "/api/dns")).json()).devices.length === 0, "with every device revoked the list says «Устройств пока нет»: " + J(d.rows));

  // ── «Пользователи → Пространства» ──────────────────────────────────────────
  await ctl({ reset: true });
  await go(page, "users/spaces");
  const sp = () => page.evaluate(() => ({ cards: [...document.querySelectorAll("#spGrid section.card.group:not(.new-card)")].map(c => ({ label: c.getAttribute("aria-label"), name: c.querySelector(".group-name").textContent,
      on: c.querySelector('[role="switch"]').getAttribute("aria-checked"), sw: c.querySelector('[role="switch"]').getAttribute("aria-label"), off: c.classList.contains("off"), count: c.querySelector(".group-count").textContent,
      chips: [...c.querySelectorAll(".chip")].map(x => x.dataset.item + " " + ((x.querySelector(".chip-sub") || {}).textContent || "")) })),
    form: !!document.querySelector("#spGrid .new-card"), empty: (document.querySelector("#spGrid .card.empty") || {}).textContent || null,
    addOff: document.getElementById("spAdd").disabled, busy: document.getElementById("page").classList.contains("busy") }));
  const idle = () => wait(page, () => !document.getElementById("page").classList.contains("busy"), 5000);
  const saves = () => sent("/api/spaces", "POST");
  const lastSave = async () => { const a = await saves(); return a.length ? a[a.length - 1].body : null; };
  const serverSpaces = async () => (await (await fetch(BASE + "/api/spaces")).json()).spaces;
  let v = await sp();
  ok(v.cards.length === 1 && v.cards[0].label === "Пространство Игры" && v.cards[0].name === "Игры" && v.cards[0].count === "2" && v.cards[0].chips.join() === "ivan_laptop 172.16.17.5,max_phone 172.16.17.7",
    "one card per space: «Игры» with its two members and their addresses: " + J(v.cards));
  ok(v.cards[0].on === "true" && !v.cards[0].off && v.cards[0].sw === "Включено: Игры" && !v.addOff && !v.form && v.empty === null, "the space is on, «Новое пространство» is available: " + J({ on: v.cards[0].on, sw: v.cards[0].sw, addOff: v.addOff }));
  ok((await where(page)).tab.join() === "Пространства" && (await where(page)).title === "awgscale — Пользователи", "the page is the «Пространства» tab of «Пользователи»");

  await page.click('#spGrid section[aria-label="Пространство Игры"] [role="switch"]'); await idle(); await sleep(200);
  v = await sp(); let b = await lastSave();
  ok(v.cards[0].on === "false" && v.cards[0].off && v.cards[0].sw === "Выключено: Игры" && (await saves()).length === 1 && b.spaces.length === 1 && b.spaces[0].on === false && b.spaces[0].id === "s00001" && b.spaces[0].members.join() === "ivan_laptop,max_phone",
    "the switch turns the space off (one save, members untouched) and the card greys out: " + J({ card: v.cards[0], body: b }));
  await page.click('#spGrid section[aria-label="Пространство Игры"] [role="switch"]'); await idle(); await sleep(200);
  v = await sp();
  ok(v.cards[0].on === "true" && !v.cards[0].off && (await serverSpaces())[0].on === true, "the switch turns it on again: " + J(v.cards[0]));

  const plus = 'button[aria-label="Добавить пользователя в «Игры»"]';
  await page.click(plus); await sleep(300);
  let m = await menu(page);
  ok(m.join() === "anna_phone,tv_gostinaya,olga_ipad,petr_phone,printer_office,router_dacha,tv_dacha,guest_1,new_phone", "«+» offers every user who is not in the space yet: " + m.join());
  ok(await page.$eval("#menu button .sub", e => e.textContent) === "172.16.17.2", "each offered user comes with the address: " + await page.$eval("#menu button .sub", e => e.textContent));
  await pick(page, "anna_phone"); await idle(); v = await sp(); b = await lastSave();
  ok(v.cards[0].chips.join() === "ivan_laptop 172.16.17.5,max_phone 172.16.17.7,anna_phone 172.16.17.2" && v.cards[0].count === "3" && b.spaces[0].members.join() === "ivan_laptop,max_phone,anna_phone",
    "a picked user becomes a member: chip, count 3, saved: " + J({ chips: v.cards[0].chips, sent: b.spaces[0].members }));
  await page.click(plus); await sleep(300); m = await menu(page);
  ok(!m.includes("anna_phone") && m.length === 8, "the new member is no longer offered: " + m.join());
  await page.keyboard.press("Escape"); await sleep(200);
  ok(await page.$eval("#menu", e => e.hidden), "Escape closes the menu without a change");
  let before = (await saves()).length;
  // the cross on a chip is shown only to a pointer that can hover; the test browser reports none, so the cross is pressed through the DOM here
  await page.$eval('.chip-x[aria-label="Убрать из пространства: max_phone"]', e => e.click()); await idle(); await sleep(200); v = await sp(); b = await lastSave();
  ok(v.cards[0].chips.join() === "ivan_laptop 172.16.17.5,anna_phone 172.16.17.2" && v.cards[0].count === "2" && (await saves()).length === before + 1 && b.spaces[0].members.join() === "ivan_laptop,anna_phone",
    "the cross on a member removes just that member: " + J({ chips: v.cards[0].chips, sent: b.spaces[0].members }));
  await page.evaluate(() => [...document.querySelectorAll("#spGrid .chip")].find(c => c.dataset.item === "anna_phone").querySelector("button.chip-t").click()); await sleep(300);
  m = await menu(page);
  ok(m.join() === "Убрать из пространства", "a click on a member opens a menu with «Убрать из пространства»: " + m.join());
  await pick(page, "Убрать из пространства"); await idle(); v = await sp();
  ok(v.cards[0].chips.join() === "ivan_laptop 172.16.17.5" && v.cards[0].count === "1" && (await serverSpaces())[0].members.join() === "ivan_laptop", "…and that removes the member too: " + J(v.cards[0].chips));

  await page.click('button[aria-label="Действия: Игры"]'); await sleep(300); m = await menu(page);
  ok(m.join() === "Переименовать,Удалить", "the card menu offers «Переименовать» and «Удалить»: " + m.join());
  await pick(page, "Переименовать");
  ok(await page.evaluate(() => document.activeElement.classList.contains("rename") && document.activeElement.value === "Игры"), "«Переименовать» puts the name into a field, focused");
  await page.keyboard.type("Игры и кино"); await page.keyboard.press("Enter"); await idle(); await sleep(200); v = await sp(); b = await lastSave();
  ok(v.cards[0].name === "Игры и кино" && v.cards[0].label === "Пространство Игры и кино" && b.spaces[0].name === "Игры и кино" && b.spaces[0].id === "s00001" && v.cards[0].chips.length === 1, "Enter saves the new name, the space keeps its id and members: " + J({ name: v.cards[0].name, sent: b.spaces[0] }));

  before = (await saves()).length;
  await page.click("#spAdd"); await sleep(300); v = await sp();
  ok(v.form && await page.evaluate(() => document.activeElement.getAttribute("aria-label") === "Название пространства"), "«Новое пространство» opens a name field, focused");
  await page.click("#spGrid .new-card button.btn.primary"); await sleep(400);
  ok((await saves()).length === before && (await sp()).form, "«Создать» with an empty name sends nothing and keeps the field open");
  await page.keyboard.type("   "); await page.keyboard.press("Enter"); await sleep(400);
  ok((await saves()).length === before && (await sp()).form, "a name of spaces only is not sent either");
  await page.keyboard.press("Escape"); await sleep(300); v = await sp();
  ok(!v.form && v.cards.length === 1 && (await saves()).length === before, "Escape closes the field, nothing is created");
  await page.click("#spAdd"); await sleep(200); await page.keyboard.type("  Семья  "); await page.keyboard.press("Enter"); await idle(); await sleep(200);
  v = await sp(); b = await lastSave();
  ok(!v.form && v.cards.map(c => c.name).join() === "Семья,Игры и кино" && v.cards[0].on === "true" && v.cards[0].count === "0" && v.cards[0].chips.length === 0, "a named space is created first in the list, on and empty: " + J(v.cards.map(c => [c.name, c.on, c.count])));
  ok((await saves()).length === before + 1 && b.spaces.length === 2 && b.spaces[0].name === "Семья" && b.spaces[0].id === null && b.spaces[1].id === "s00001" && b.spaces[1].members.join() === "ivan_laptop", "it is sent as one save together with the untouched existing space: " + J(b.spaces));
  await page.click('button[aria-label="Добавить пользователя в «Семья»"]'); await sleep(300); await pick(page, "tv_dacha"); await idle(); v = await sp();
  ok(v.cards[0].chips.join() === "tv_dacha 172.16.17.10" && v.cards[1].chips.join() === "ivan_laptop 172.16.17.5", "a member added to the new space lands in it and not in the other: " + J(v.cards.map(c => c.chips)));

  await ctl({ fail: "/api/spaces" }); await clearToasts(page);
  await page.click('#spGrid section[aria-label="Пространство Семья"] [role="switch"]'); await idle(); await sleep(300); v = await sp(); t = await toasts(page);
  ok(/сервер отказал/.test(t.join()) && v.cards[0].on === "true" && !v.cards[0].off && (await serverSpaces())[0].on === true, "a save refused by the server is reported and the card shows what is really stored: " + J({ t, on: v.cards[0].on }));

  // another window changes the spaces
  let srvNow = await serverSpaces();
  await ctl({ set: { spaces: srvNow.concat([{ id: "s00777", name: "Из другого окна", on: false, members: ["guest_1"] }]) } });
  const sawOther = await wait(page, () => !!document.querySelector('#spGrid section[aria-label="Пространство Из другого окна"]'), 9000);
  v = await sp();
  ok(sawOther && v.cards.map(c => c.name).join() === "Семья,Игры и кино,Из другого окна" && v.cards[2].off, "an idle page picks up a change made in another window by itself: " + J(v.cards.map(c => c.name)));
  await clearToasts(page);
  await page.click('button[aria-label="Добавить пользователя в «Семья»"]'); await sleep(300);          // an open menu holds the list still
  srvNow = await serverSpaces(); srvNow[1].members = ["ivan_laptop", "petr_phone"]; srvNow[2].name = "Чужое";
  await ctl({ set: { spaces: srvNow } });
  before = (await saves()).length;
  await pick(page, "olga_ipad"); await idle(); await sleep(300);
  t = await toasts(page); v = await sp(); const after = await serverSpaces();
  ok(/пространства изменились в другом окне/.test(t.join()), "a save based on an outdated list is refused with «пространства изменились в другом окне»: " + J(t));
  ok((await saves()).length === before + 1 && J(after) === J(srvNow), "the refused save changed nothing on the server: " + J(after.map(x => [x.name, x.members])));
  ok(v.cards.map(c => c.name).join() === "Семья,Игры и кино,Чужое" && v.cards[1].chips.join() === "ivan_laptop 172.16.17.5,petr_phone 172.16.17.6" && v.cards[0].chips.join() === "tv_dacha 172.16.17.10",
    "after the refusal the page shows the list as it is stored now, without the refused change: " + J(v.cards.map(c => [c.name, c.chips])));
  await page.click('button[aria-label="Добавить пользователя в «Семья»"]'); await sleep(300); await pick(page, "olga_ipad"); await idle(); await sleep(200); v = await sp();
  ok(v.cards[0].chips.join() === "tv_dacha 172.16.17.10,olga_ipad 172.16.17.4" && (await serverSpaces())[0].members.join() === "tv_dacha,olga_ipad" && (await serverSpaces())[1].members.join() === "ivan_laptop,petr_phone",
    "the same change repeated on the fresh list goes through and keeps the other window's change: " + J(v.cards.map(c => c.chips)));

  // delete
  before = (await saves()).length;
  await page.click('button[aria-label="Действия: Чужое"]'); await sleep(300); await pick(page, "Удалить"); await idle(); await sleep(200); v = await sp(); b = await lastSave();
  ok(v.cards.map(c => c.name).join() === "Семья,Игры и кино" && (await saves()).length === before + 1 && b.spaces.map(x => x.id).join() === (await serverSpaces()).map(x => x.id).join() && !b.spaces.some(x => x.id === "s00777"),
    "«Удалить» (confirmed) removes that space and only it: " + J(v.cards.map(c => c.name)));
  for (const n of ["Семья", "Игры и кино"]) { await page.click(`button[aria-label="Действия: ${n}"]`); await sleep(300); await pick(page, "Удалить"); await idle(); await sleep(200); }
  v = await sp();
  ok(v.cards.length === 0 && v.empty === "Пространств пока нет" && (await serverSpaces()).length === 0 && !v.addOff, "with the last space deleted the page says «Пространств пока нет»: " + J({ empty: v.empty }));
  await ctl({ set: { spaces: Array.from({ length: 20 }, (_, i) => ({ id: "s9" + String(i).padStart(4, "0"), name: "Пр " + (i + 1), on: true, members: [] })) } });
  const sawFull = await wait(page, () => document.querySelectorAll("#spGrid section.card.group").length === 20, 9000);
  ok(sawFull && (await sp()).addOff, "at the limit of spaces (20) «Новое пространство» is switched off: " + J({ cards: (await sp()).cards.length, addOff: (await sp()).addOff }));

  // ── declined confirmations, then phone width (a second tab that answers «Отмена») ──
  await ctl({ reset: true });
  await ctl({ set: { dns_devices: [dev("tv_kitchen", "k3x9q2w8e7r6AbCdEfGhIjKl", 600, 1200, true), dev("device_with_a_very_long_name_32c", "Ll11ll22ll33ll44ll55ll66", 0, 0, false)],
    spaces: [{ id: "s00001", name: "Игры", on: true, members: ["ivan_laptop", "max_phone"] }, { id: "s00002", name: "Оченьдлинноеназваниепространствабезпробел", on: true, members: ["anna_phone", "tv_gostinaya", "olga_ipad", "printer_office", "router_dacha"] }],
    journal: [{ t: now() - 90, text: "В «Работа» добавлено: us-east-1.console.aws.amazon.com, id.atlassian.com", bad: false, kind: "change", who: "panel" },
      { t: now() - 60, text: "Создано пространство «Оченьдлинноеназваниепространствабезпробел»", bad: true, kind: "change", who: "panel" },
      { t: now() - 30, text: "Создан пользователь new_phone", bad: false, kind: "change", who: "bot" }] } });
  await page.close();
  const no = await open(browser, "dns", { dismiss: true });
  await no.click('#dnsBody button[aria-label="Действия с устройством tv_kitchen"]'); await sleep(300); await pick(no, "Отозвать"); await sleep(600);
  ok((await changes()).length === 0 && (await no.$$eval("#dnsBody .uname", a => a.map(e => e.textContent))).join() === "tv_kitchen,device_with_a_very_long_name_32c" && (await toasts(no)).length === 0, "«Отозвать» answered with «Отмена» sends nothing and keeps the device: " + J((await changes()).map(x => x.method + " " + x.path)));
  await no.click('#nav a[data-sec="users"]'); await sleep(300); await no.evaluate(() => { location.hash = "#/users/spaces"; });
  await wait(no, () => document.querySelectorAll("#spGrid section.card.group").length === 2, 5000);
  await no.click('button[aria-label="Действия: Игры"]'); await sleep(300); await pick(no, "Удалить"); await sleep(600);
  ok((await changes()).length === 0 && await no.$('#spGrid section[aria-label="Пространство Игры"]') !== null, "«Удалить» of a space answered with «Отмена» sends nothing and keeps the space: " + J((await changes()).map(x => x.method + " " + x.path)));

  await no.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  await go(no, "users/spaces"); await wait(no, () => document.querySelectorAll("#spGrid section.card.group").length === 2, 5000);
  let n = await narrow(no);
  ok(fits(n), "phone width (390): «Пространства», with a 40-letter name and five members, does not scroll sideways: " + J(n));
  await no.click("#spAdd"); await sleep(300); n = await narrow(no);
  ok(fits(n) && await no.$("#spGrid .new-card") !== null, "…also with the «Новое пространство» field open: " + J(n));
  await no.click('button[aria-label^="Добавить пользователя в «Оч"]'); await sleep(300);
  const mb = await no.evaluate(() => { const r = document.getElementById("menu").getBoundingClientRect(); return { l: Math.round(r.left), r: Math.round(r.right), w: innerWidth, hidden: document.getElementById("menu").hidden }; });
  ok(!mb.hidden && mb.l >= 0 && mb.r <= mb.w, "…and the «+» menu opens fully inside the screen: " + J(mb));
  await no.keyboard.press("Escape");
  await go(no, "dns"); await wait(no, () => document.querySelectorAll("#dnsBody .uname").length === 2, 5000);
  n = await narrow(no);
  ok(fits(n), "phone width: «DNS без VPN», with a 32-letter device name, does not scroll sideways: " + J(n));
  const acts = await no.evaluate(() => [...document.querySelectorAll("#dnsBody tr")].map(tr => { const r = tr.querySelector('button[aria-haspopup="menu"]').getBoundingClientRect(); return Math.round(r.right); }));
  ok(acts.every(x => x <= 390) && await no.evaluate(() => [...document.querySelectorAll("#dnsBody .c-narrow")].every(e => e.offsetParent !== null && e.textContent.length > 0)), "…each row keeps its buttons on the screen and shows the status under the name: " + J(acts));
  await no.click('#dnsBody button[data-k="device_with_a_very_long_name_32c:addr"]'); await sleep(500); n = await narrow(no);
  ok(fits(n) && await no.$eval("#dnsPanel", e => !e.hidden && e.getBoundingClientRect().right <= innerWidth), "…also with the address panel open: " + J(n));
  await go(no, "servers"); await wait(no, () => document.querySelectorAll("#srvBody tr").length === 3, 5000); n = await narrow(no);
  ok(fits(n), "phone width: «Серверы» does not scroll sideways (the wide table scrolls inside its own frame): " + J(n));
  await go(no, "journal"); await wait(no, () => document.querySelectorAll("#jrBody tr").length === 3, 5000); n = await narrow(no);
  ok(fits(n), "phone width: «Журнал», with a site name and a 40-letter space name in the entries, does not scroll sideways: " + J(n));
  const jtxt = await no.evaluate(() => [...document.querySelectorAll("#jrBody tr")].map(tr => { const r = tr.lastElementChild.getBoundingClientRect(), w = tr.closest(".table-wrap"); return { l: Math.round(r.left), inner: w.scrollWidth, box: w.clientWidth }; }));
  ok(jtxt.every(x => x.l < 390), "…and the text of each entry starts on the screen: " + J(jtxt));
  ok(jtxt.every(x => x.inner <= x.box), "…and the entries can be read in full without scrolling the journal sideways (long names wrap): the journal is " + jtxt[0].inner + "px wide in a frame of " + jtxt[0].box + "px");
  st = await status(no);
  ok(st.text === "Всё работает" && await no.$eval("#status", e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right <= innerWidth && r.left >= 0; }), "…and the status line is still on the screen: " + J(st));
  await finish(browser, [no]);
});
