// «Пользователи → Список»: groups as folding rows, search, the «+» of a group, the user menu,
// the group menu, «Новая группа», a stale save from a second tab, a server refusal, phone width.
const { BASE, sleep, ok, ctl, apiLog, changes, connect, open, menu, pick, toasts, finish, run } = require("./lib");
run(async () => {
  const browser = await connect();
  const page = await open(browser, "users");
  const said = [];                                            // the texts of the confirmations the page asked
  page.on("dialog", d => said.push(d.message()));
  const J = x => JSON.stringify(x);
  // ── readers ──
  const cards = p => p.evaluate(() => [...document.querySelectorAll("#userBlocks section[data-g]")].map(s => {
    const r = s.getBoundingClientRect(), f = s.querySelector(".fold"), q = c => (s.querySelector(c) || {}).textContent || "";
    return { key: s.dataset.g, name: q(".fold span"), count: q(".group-count"), traf: q(".group-traf"), trafTitle: (s.querySelector(".group-traf") || {}).title || "", open: s.classList.contains("open"), expanded: f.getAttribute("aria-expanded"), hidden: s.hidden,
      top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height),
      badges: [...s.querySelectorAll(".group-head .badge")].map(b => b.textContent), space: (s.querySelector(".group-head .btn.small") || { getAttribute: () => null }).getAttribute("aria-pressed"),
      rows: [...s.querySelectorAll("tbody tr .uname")].map(e => e.textContent) };
  }));
  const card = async (p, key) => (await cards(p)).find(c => c.key === key) || null;
  const row = (p, name) => p.evaluate(name => {
    const tr = [...document.querySelectorAll("#userBlocks tbody tr")].find(t => (t.querySelector(".uname") || {}).textContent === name);
    if (!tr) return null;
    const q = c => (tr.querySelector(c) || {}).textContent || "";
    return { group: tr.closest("section").dataset.g, badge: q(".name-cell .badge"), st: q("td.c-st"), exit: q("td.c-exit"), traf: q("td.c-traf"), trafTitle: (tr.querySelector("td.c-traf") || {}).title || "", sel: tr.classList.contains("sel"), wait: !tr.querySelector("button") };
  }, name);
  const panel = p => p.evaluate(() => {
    const el = document.getElementById("codePanel"); if (!el || el.hidden) return null;
    const seg = [...el.querySelectorAll(".seg button")];
    return { name: (el.querySelector("h2") || {}).textContent, ip: (document.getElementById("codeIp") || {}).textContent, tabs: seg.map(b => b.textContent), tab: (seg.find(b => b.getAttribute("aria-pressed") === "true") || {}).textContent,
      uri: (document.getElementById("codeUri") || {}).value || "", qr: (el.querySelector("img.qr") || { alt: "" }).alt, buttons: [...el.querySelectorAll(".row .btn")].map(b => b.textContent),
      diag: [...el.querySelectorAll(".diag-row")].map(d => d.textContent), diagText: (el.querySelector(".diag") || {}).textContent || "" };
  });
  const srv = async path => (await fetch(BASE + path)).json();
  const groupsNow = async () => (await srv("/api/ugroups")).groups;
  const home = async name => ((await groupsNow()).find(g => g.members.includes(name)) || { id: "" }).id;      // the group a user is in on the server
  let mark = 0;                                               // the changing requests sent since the last look
  const sent = async () => { const all = await changes(); const out = all.slice(mark); mark = all.length; return out; };
  const brief = list => list.map(x => x.method + " " + x.path).join(", ");
  const inGroup = (body, id) => ((body.groups || []).find(g => g.id === id) || { members: [] }).members;
  const ddmm = (p, days) => p.evaluate(days => { const d = new Date(Date.now() + days * 86400000); return String(d.getDate()).padStart(2, "0") + "." + String(d.getMonth() + 1).padStart(2, "0"); }, days);
  // ── actions ──
  const idle = async p => { await p.waitForFunction(() => !document.getElementById("page").classList.contains("busy"), { timeout: 8000 }).catch(() => {}); await sleep(200); };
  const fold = async (p, key) => { await p.click(`#userBlocks .fold[data-k="fold:${key}"]`); await sleep(250); };
  const unfold = async (p, key) => { if (!(await card(p, key)).open) await fold(p, key); };
  const userDots = name => `[data-k="${name}:more"]`, groupDots = key => `section[data-g="${key}"] .group-head button[aria-haspopup="menu"]`;
  // open a menu and walk through it: act(page, selector, "Срок", "7 дней")
  const act = async (p, sel, ...labels) => {
    await p.click(sel); await sleep(250);
    for (const l of labels) if (!(await pick(p, l))) { await p.keyboard.press("Escape"); return false; }
    await idle(p); return true;
  };
  const labelsOf = async (p, sel) => { await p.click(sel); await sleep(250); const m = await menu(p); await p.keyboard.press("Escape"); await sleep(150); return m; };
  const restart = async (...pages) => { await ctl({ reset: true }); mark = 0; for (const p of pages) { await p.reload({ waitUntil: "networkidle0" }); await sleep(400); } };
  const type = async (p, sel, text) => { await p.click(sel, { clickCount: 3 }); await p.keyboard.press("Backspace"); if (text) await p.type(sel, text); await sleep(250); };
  const SUBMIT = "#userBlocks .addrow button[type=submit]";
  const addErr = p => p.evaluate(() => (document.getElementById("ugAddErr") || {}).textContent || "");
  const fieldIn = p => p.evaluate(() => { const f = document.getElementById("ugAddInput"); return f ? f.closest("section").dataset.g : null; });

  // ── 1. the groups as rows ──
  let cs = await cards(page);
  ok(cs.map(c => c.name).join() === "Дом,Офис,Дача,Без группы", "the list shows the three groups and «Без группы»: " + cs.map(c => c.name).join());
  ok(cs.every(c => !c.open && c.expanded === "false" && c.rows.length === 0), "every group is folded at first: " + J(cs.map(c => [c.name, c.open, c.rows.length])));
  const boxW = await page.$eval("#userBlocks", e => Math.round(e.getBoundingClientRect().width));
  ok(cs.every(c => c.width === boxW && c.left === cs[0].left) && boxW > 600 && cs.every((c, i) => !i || c.top >= cs[i - 1].top + cs[i - 1].height), "each group is a row of the full width of the list (" + boxW + "px), one under another: " + J(cs.map(c => [c.left, c.top, c.width])));
  ok(cs.map(c => c.count).join("|") === "4 · 3 онлайн|3 · 1 онлайн|2 · 1 онлайн|2", "a folded group says how many users it has and how many are online: " + cs.map(c => c.count).join(" | "));
  // (this check used to expect the counters since the tunnel started — "122 ГБ|20,4 ГБ|56,5 ГБ|296 МБ"; those are now the fallback, checked at the end)
  ok(cs.map(c => c.traf).join("|") === "20,2 ГБ|3,4 ГБ|9,3 ГБ|48,8 МБ" && cs.every(c => c.trafTitle === "Трафик за 30 дней"), "a folded group shows the traffic of all its users over the last 30 days, and its tooltip says so: " + cs.map(c => c.traf).join(" | ") + " — " + cs[0].trafTitle);
  // a click on the header line, away from the arrow and the name
  const headPoint = (p, key, where) => p.evaluate((key, where) => {
    const hd = document.querySelector(`section[data-g="${key}"] .group-head`), hr = hd.getBoundingClientRect(), r = hd.querySelector(where).getBoundingClientRect();
    return { x: r.left + r.width / 2, y: hr.top + hr.height / 2, w: r.width };
  }, key, where);
  const tops = async () => ({ t: (await cards(page)).slice(0, 3).map(c => c.top), y: await page.evaluate(() => window.scrollY) });
  // a low window with two groups unfolded above, scrolled down: only then can the page jump
  await page.setViewport({ width: 1360, height: 420, deviceScaleFactor: 1 }); await sleep(300);
  await fold(page, "g00001"); await fold(page, "g00002");
  ok((await card(page, "g00001")).open && (await card(page, "g00002")).open, "a click on the name with the arrow unfolds («Дом», «Офис»)");
  await page.evaluate(() => { const hd = document.querySelector('section[data-g="g00003"] .group-head'); window.scrollTo(0, Math.round(hd.getBoundingClientRect().top + window.scrollY - 250)); }); await sleep(400);
  let before = await tops(), pt = await headPoint(page, "g00003", ".spacer");
  ok(before.y > 0 && before.t[2] > 150 && before.t[2] < 380, "(the page is scrolled down, «Дача» is low in the window: " + J(before) + ")");
  await page.mouse.click(pt.x, pt.y); await sleep(400);
  let c3 = await card(page, "g00003"), after = await tops();
  ok(c3.open && c3.expanded === "true" && c3.rows.join() === "router_dacha,tv_dacha", "a click on the empty part of the header line of «Дача» unfolds it: its users are " + c3.rows.join() + " (free part " + Math.round(pt.w) + "px wide)");
  ok(J(before) === J(after), "unfolding «Дача» moves neither the groups above nor the page: " + J(before) + " → " + J(after));
  ok((await cards(page)).filter(c => c.open).map(c => c.name).join() === "Дом,Офис,Дача", "the other groups stay as they were: unfolded are " + (await cards(page)).filter(c => c.open).map(c => c.name).join());
  let r = await row(page, "router_dacha");
  ok(r && /онлайн/.test(r.st) && r.exit === "exit1-nl" && r.traf === "7,4 ГБ" && r.trafTitle === "Трафик за 30 дней", "an unfolded row shows status, exit and the user's traffic over 30 days (not the 44,7 ГБ since the tunnel started): " + J(r));
  pt = await headPoint(page, "g00003", ".group-count"); await page.mouse.click(pt.x, pt.y); await sleep(400);
  c3 = await card(page, "g00003"); after = await tops();
  ok(!c3.open && c3.rows.length === 0 && J(before) === J(after), "a click on the counter in the same line folds it back, nothing above moves: open=" + c3.open + " " + J(after));
  pt = await headPoint(page, "g00003", ".group-traf"); await page.mouse.click(pt.x, pt.y); await sleep(400);
  after = await tops();
  ok((await card(page, "g00003")).open && J(before) === J(after), "a click on the traffic figure of the line unfolds as well, again in place: " + J(after));
  await fold(page, "g00003"); await fold(page, "g00001");
  ok(!(await card(page, "g00001")).open && !(await card(page, "g00003")).open && (await card(page, "g00002")).open, "a click on the name folds back too («Дом», «Дача»)");
  await page.setViewport({ width: 1360, height: 900, deviceScaleFactor: 1 }); await page.evaluate(() => window.scrollTo(0, 0)); await sleep(300);
  pt = await headPoint(page, "", ".spacer"); await page.mouse.click(pt.x, pt.y); await sleep(300);
  ok((await card(page, "")).rows.join() === "guest_1,new_phone", "«Без группы» unfolds the same way and lists those in no group: " + (await card(page, "")).rows.join());
  await page.reload({ waitUntil: "networkidle0" }); await sleep(500);
  cs = await cards(page);
  ok(cs.filter(c => c.open).map(c => c.name).join() === "Офис,Без группы" && cs[1].rows.length === 3, "after a reload the same groups are unfolded: " + cs.filter(c => c.open).map(c => c.name).join());
  ok((await sent()).length === 0, "folding and unfolding sends nothing to the server");

  // ── 2. search ──
  await fold(page, "");                                       // «Офис» stays unfolded, the rest folded
  await page.type("#userSearch", "phone"); await sleep(300);
  cs = await cards(page);
  ok(cs.filter(c => !c.hidden).map(c => c.name).join() === "Дом,Офис,Без группы", "search «phone»: only the groups with a match are left: " + cs.filter(c => !c.hidden).map(c => c.name).join());
  ok(cs.filter(c => !c.hidden).every(c => c.open), "search: the groups with a match are unfolded: " + J(cs.filter(c => !c.hidden).map(c => [c.name, c.open])));
  ok(cs[0].rows.join() === "anna_phone,max_phone" && cs[1].rows.join() === "petr_phone" && cs[3].rows.join() === "new_phone", "search: only the matching users are listed: " + J(cs.map(c => c.rows)));
  await type(page, "#userSearch", "ZZZ-nobody");
  ok((await cards(page)).every(c => c.hidden), "a search with no match leaves no group: " + (await cards(page)).filter(c => !c.hidden).map(c => c.name).join());
  await type(page, "#userSearch", "TV_");
  cs = await cards(page);
  ok(cs.filter(c => !c.hidden).map(c => c.name).join() === "Дом,Дача" && cs[0].rows.join() === "tv_gostinaya" && cs[2].rows.join() === "tv_dacha", "search does not depend on the letter case («TV_»): " + J(cs.filter(c => !c.hidden).map(c => [c.name, c.rows])));
  await type(page, "#userSearch", "");
  cs = await cards(page);
  ok(cs.every(c => !c.hidden) && cs.filter(c => c.open).map(c => c.name).join() === "Офис" && cs[1].rows.length === 3, "clearing the search brings everything back as it was (only «Офис» unfolded, all its 3 users): " + J(cs.map(c => [c.name, c.hidden, c.open, c.rows.length])));
  ok((await sent()).length === 0, "searching sends nothing to the server");

  // ── 3. the «+» of a group ──
  const plus3 = 'section[data-g="g00003"] .group-head button[aria-label="Добавить в «Дача»"]';
  ok(!!(await page.$(plus3)), "the «+» of a group is named after it («Добавить в «Дача»»)");
  await page.click(plus3); await sleep(300);
  ok((await fieldIn(page)) === "g00003" && await page.evaluate(() => document.activeElement && document.activeElement.id === "ugAddInput") && !(await card(page, "g00003")).open, "«+» of «Дача» opens a name field inside that card (the card itself stays folded) and puts the cursor in it: field in " + J(await fieldIn(page)));
  await page.type("#ugAddInput", "bad name!"); await page.click(SUBMIT); await sleep(400);
  let s = await sent();
  ok(/латиница/.test(await addErr(page)) && s.length === 0 && (await fieldIn(page)) === "g00003", "an invalid name shows an error under the field and sends nothing: «" + (await addErr(page)) + "», sent: " + brief(s));
  await page.focus("#ugAddInput"); await page.keyboard.press("Escape"); await sleep(300);
  ok((await fieldIn(page)) === null && (await sent()).length === 0, "Escape closes the field, nothing is sent");
  await unfold(page, "g00003"); await page.click(plus3); await sleep(300);
  await page.type("#ugAddInput", "kid_tablet"); await page.click(SUBMIT); await idle(page); await sleep(400);
  s = await sent();
  ok(brief(s) === "POST /api/clients, POST /api/ugroups" && s[0].body.name === "kid_tablet", "a new name in the field of «Дача» creates the user and then saves the groups: " + brief(s) + " " + J(s[0] && s[0].body));
  ok(s[1] && inGroup(s[1].body, "g00003").join() === "router_dacha,tv_dacha,kid_tablet" && s[1].body.groups.filter(g => g.members.includes("kid_tablet")).length === 1 && s[1].body.groups.length === 3,
    "the save puts the new user into «Дача» and nowhere else: " + J(s[1] && s[1].body.groups.map(g => [g.name, g.members])));
  ok((await home("kid_tablet")) === "g00003", "on the server kid_tablet is in «Дача»: " + (await home("kid_tablet")));
  let pn = await panel(page);
  ok(pn && pn.name === "kid_tablet" && pn.tabs.join() === "AmneziaVPN,AmneziaWG,Проверка", "creating a user opens the code panel for it: " + J(pn && [pn.name, pn.tabs]));
  await page.type("#ugAddInput", "petr_phone"); await page.click(SUBMIT); await idle(page); await sleep(300);
  s = await sent();
  ok(brief(s) === "POST /api/ugroups" && inGroup(s[0].body, "g00003").includes("petr_phone") && !inGroup(s[0].body, "g00002").includes("petr_phone"), "an existing user's name moves that user here with one save and creates nobody: " + brief(s) + " " + J(s[0] && s[0].body.groups.map(g => [g.name, g.members])));
  cs = await cards(page);
  ok(cs[2].rows.includes("petr_phone") && !cs[1].rows.includes("petr_phone") && cs[1].count === "2 · 1 онлайн", "the moved user is shown in «Дача» and no longer in «Офис» (" + cs[1].count + "): " + J([cs[1].rows, cs[2].rows]));
  await page.type("#ugAddInput", "tv_dacha"); await page.click(SUBMIT); await sleep(400);
  s = await sent();
  ok(/Уже в этой группе/.test(await addErr(page)) && s.length === 0, "the name of somebody already in the group is refused in place: «" + (await addErr(page)) + "», sent: " + brief(s));
  await page.focus("#ugAddInput"); await page.keyboard.press("Escape"); await sleep(300);
  const plus0 = 'section[data-g=""] .group-head button[aria-label="Новый пользователь"]';
  ok(!!(await page.$(plus0)), "«Без группы» has its own «+» («Новый пользователь»)");
  await page.click(plus0); await sleep(300);
  ok((await fieldIn(page)) === "", "the «+» of «Без группы» opens the field in that card: " + J(await fieldIn(page)));
  await page.type("#ugAddInput", "guest_1"); await page.click(SUBMIT); await sleep(400);
  s = await sent();
  ok(/уже есть/.test(await addErr(page)) && s.length === 0, "«Без группы»: an existing name is refused, nothing is sent: «" + (await addErr(page)) + "», sent: " + brief(s));
  await type(page, "#ugAddInput", "guest_2"); await page.click(SUBMIT); await idle(page); await sleep(400);
  s = await sent(); pn = await panel(page);
  ok(brief(s) === "POST /api/clients" && s[0].body.name === "guest_2" && pn && pn.name === "guest_2" && (await home("guest_2")) === "", "«Без группы»: a new name only creates the user (no group save) and opens its code: " + brief(s) + ", panel " + J(pn && pn.name));
  await page.focus("#ugAddInput"); await page.keyboard.press("Escape"); await sleep(200);
  await unfold(page, "");
  const both = await page.waitForFunction(() => ["kid_tablet", "guest_2"].every(n => !!document.querySelector(`[data-k="${n}:more"]`)), { timeout: 15000 }).then(() => true, () => false);
  const rk = await row(page, "kid_tablet"), rg = await row(page, "guest_2");
  ok(both && rk.group === "g00003" && rg.group === "" && /не подключался/.test(rk.st), "within seconds both new users are ordinary rows in their groups: " + J([rk, rg]));
  ok((await card(page, "g00003")).count === "4 · 1 онлайн" && (await card(page, "")).count === "3", "the counters include them: «Дача» " + (await card(page, "g00003")).count + ", «Без группы» " + (await card(page, "")).count);

  // ── 4. the menu of a user ──
  const pageB = await open(browser, "users", { dismiss: true });       // a second tab; it answers «Отмена» to every question
  const saidB = []; pageB.on("dialog", d => saidB.push(d.message()));
  await restart(page, pageB); await page.bringToFront();
  await unfold(page, "g00001"); await unfold(page, "g00002");
  let m = await labelsOf(page, userDots("anna_phone"));
  ok(m.join() === "Проверить,Приостановить,Срок,Выход,В группу «Офис»,В группу «Дача»,Убрать из группы,Удалить", "the ⋮ of a user in «Дом» offers: " + m.join());
  await page.click('[data-k="anna_phone:code"]'); await sleep(600);
  pn = await panel(page);
  ok(pn && pn.name === "anna_phone" && pn.ip === "172.16.17.2" && pn.tabs.join() === "AmneziaVPN,AmneziaWG,Проверка" && pn.tab === "AmneziaVPN", "«Код» opens the side panel of that user with the tabs AmneziaVPN / AmneziaWG / Проверка: " + J(pn && [pn.name, pn.ip, pn.tabs, pn.tab]));
  ok(pn && /^vpn:\/\//.test(pn.uri) && /AmneziaVPN/.test(pn.qr) && pn.buttons.join() === "Скопировать", "the AmneziaVPN tab has the QR code, the connection string and «Скопировать»: " + J(pn && [pn.uri.slice(0, 12), pn.qr, pn.buttons]));
  let q = await page.$eval("#codePanel img.qr", e => ({ w: e.getBoundingClientRect().width, h: e.getBoundingClientRect().height, n: e.naturalWidth, fits: e.getBoundingClientRect().right <= e.parentNode.getBoundingClientRect().right }));
  ok(q.n === 484 && q.w === q.h && q.w % 121 === 0 && q.w / 121 >= 2 && q.fits, "the QR code is drawn with a whole number of pixels per module, at least two, and fits the panel: " + J(q));
  await page.click("#codePanel img.qr"); await sleep(200);
  q = await page.evaluate(() => { const b = document.querySelector(".qr-zoom"), i = b && b.querySelector("img"); return i && { w: i.getBoundingClientRect().width, bg: getComputedStyle(b).backgroundColor, vh: innerHeight }; });
  ok(q && q.w % 121 === 0 && q.w / 121 >= 6 && q.w <= q.vh && q.bg === "rgb(255, 255, 255)", "a click shows it on the whole screen on white, as large as fits: " + J(q));
  await page.keyboard.press("Escape"); await sleep(200);
  ok(await page.evaluate(() => !document.querySelector(".qr-zoom") && !!document.getElementById("codePanel").querySelector("img.qr")), "Escape closes the large code and leaves the side panel open");
  r = await row(page, "anna_phone");
  ok(r.sel && await page.$eval('[data-k="anna_phone:code"]', e => e.getAttribute("aria-pressed") === "true"), "the row whose code is open is marked");
  await page.evaluate(() => [...document.querySelectorAll("#codePanel .seg button")].find(b => b.textContent === "AmneziaWG").click()); await sleep(300);
  pn = await panel(page);
  ok(pn.tab === "AmneziaWG" && /AmneziaWG/.test(pn.qr) && pn.buttons.join() === "Скачать .conf", "the AmneziaWG tab has its own QR code and «Скачать .conf»: " + J([pn.tab, pn.qr, pn.buttons]));
  await page.evaluate(() => [...document.querySelectorAll("#codePanel .seg button")].find(b => b.textContent === "Проверка").click()); await sleep(600);
  pn = await panel(page);
  ok(pn.tab === "Проверка" && pn.diag.length === 2 && /На связи/.test(pn.diag[0]) && /Выход exit1-nl работает/.test(pn.diag[1]), "the «Проверка» tab shows the findings for the user: " + J(pn.diag));
  let log = await apiLog();
  ok(log.some(x => x.method === "GET" && x.path === "/api/clients/anna_phone/qr") && log.some(x => x.path === "/api/diag" && x.query.name === "anna_phone"), "the panel asked the server for that user's code and check");
  await page.click('#codePanel button[aria-label="Закрыть"]'); await sleep(300);
  ok((await panel(page)) === null && !(await row(page, "anna_phone")).sel, "«Закрыть» hides the panel and unmarks the row");
  ok(await act(page, userDots("petr_phone"), "Проверить"), "«Проверить» is in the menu of petr_phone");
  await sleep(500); pn = await panel(page);
  ok(pn && pn.name === "petr_phone" && pn.tab === "Проверка" && pn.diag.some(t => /Не на связи/.test(t)), "«Проверить» opens the panel on «Проверка» with what was found: " + J(pn && [pn.name, pn.tab, pn.diag]));
  await page.click('#codePanel button[aria-label="Закрыть"]'); await sleep(200);
  ok((await sent()).length === 0, "looking at codes and checks changes nothing on the server");
  // term
  await page.click(userDots("anna_phone")); await sleep(250); await pick(page, "Срок");
  m = await menu(page);
  ok(m.join() === "Без срока,1 день,7 дней,30 дней", "«Срок» offers: " + m.join());
  await pick(page, "7 дней"); await idle(page);
  let d7 = await ddmm(page, 7), ts = await toasts(page);
  s = await sent(); r = await row(page, "anna_phone");
  ok(brief(s) === "POST /api/clients/expire" && J(s[0].body) === J({ names: ["anna_phone"], expires: "7d" }), "«Срок → 7 дней» sends one request for that user: " + brief(s) + " " + J(s[0] && s[0].body));
  ok(r.badge === "до " + d7 && ts.some(t => t === "anna_phone: до " + d7), "the row gets the badge «до " + d7 + "» at once: badge «" + r.badge + "», notes " + J(ts));
  await sleep(5600); r = await row(page, "anna_phone");
  ok(r.badge === "до " + d7, "the badge is still there after the next reading of the server: «" + r.badge + "»");
  ok(await act(page, userDots("anna_phone"), "Срок", "Без срока"), "«Срок → Без срока» is offered");
  s = await sent(); r = await row(page, "anna_phone");
  const cl = async name => (await srv("/api/clients?names=" + name)).clients.find(c => c.name === name);
  ok(brief(s) === "POST /api/clients/expire" && s[0].body.expires === "" && r.badge === "" && (await cl("anna_phone")).expires_at === null, "«Без срока» clears the term: " + brief(s) + " " + J(s[0] && s[0].body) + ", badge «" + r.badge + "»");
  // an exit by hand
  await page.click(userDots("anna_phone")); await sleep(250); await pick(page, "Выход"); await sleep(250);
  m = await menu(page); await page.keyboard.press("Escape"); await sleep(150);
  ok(m.join() === "Авто,exit1-nl,exit2-nl", "«Выход» of a user offers «Авто» and every exit: " + m.join());
  ok(await act(page, userDots("anna_phone"), "Выход", "exit2-nl"), "«Выход → exit2-nl» is offered");
  s = await sent();
  ok(brief(s) === "POST /api/clients/exit" && J(s[0].body) === J({ names: ["anna_phone"], exit: 3 }) && (await cl("anna_phone")).pin === 3, "it sends one request with the user and the exit's number: " + brief(s) + " " + J(s[0] && s[0].body));
  await act(page, userDots("anna_phone"), "Выход", "exit2-nl");
  ok((await sent()).length === 0, "choosing the same exit again sends nothing");
  ok(await act(page, userDots("anna_phone"), "Выход", "Авто"), "«Выход → Авто» is offered");
  s = await sent();
  ok(brief(s) === "POST /api/clients/exit" && s[0].body.exit === null && (await cl("anna_phone")).pin === null, "«Авто» takes the choice back: " + brief(s) + " " + J(s[0] && s[0].body));
  // pause
  ok(await act(page, userDots("anna_phone"), "Приостановить"), "«Приостановить» is offered");
  s = await sent(); r = await row(page, "anna_phone");
  ok(brief(s) === "POST /api/clients/suspend" && J(s[0].body) === J({ names: ["anna_phone"], on: true }) && r.st === "пауза", "«Приостановить» sends one request and the status becomes «пауза»: " + brief(s) + " " + J(s[0] && s[0].body) + ", status «" + r.st + "»");
  m = await labelsOf(page, userDots("anna_phone"));
  ok(m.includes("Возобновить") && !m.includes("Приостановить"), "a paused user is offered «Возобновить»: " + m.join());
  await act(page, userDots("anna_phone"), "Возобновить");
  s = await sent(); r = await row(page, "anna_phone");
  ok(brief(s) === "POST /api/clients/suspend" && s[0].body.on === false && r.st === "онлайн" && (await cl("anna_phone")).suspended === false, "«Возобновить» brings the user back: " + brief(s) + ", status «" + r.st + "»");
  // to another group, out of the group
  const v0 = (await srv("/api/ugroups")).v;
  ok(await act(page, userDots("anna_phone"), "В группу «Офис»"), "«В группу «Офис»» is offered");
  s = await sent(); cs = await cards(page);
  ok(brief(s) === "POST /api/ugroups" && s[0].body.base === v0 && s[0].body.groups.map(g => g.id).join() === "g00001,g00002,g00003" && !inGroup(s[0].body, "g00001").includes("anna_phone") && inGroup(s[0].body, "g00002").includes("anna_phone"),
    "moving sends one save based on the current list, with every group in it and the user only in «Офис»: " + brief(s) + " " + J(s[0] && s[0].body.groups.map(g => [g.id, g.members])));
  ok(cs[0].count === "3 · 2 онлайн" && cs[1].count === "4 · 2 онлайн" && cs[1].rows.includes("anna_phone") && !cs[0].rows.includes("anna_phone") && (await home("anna_phone")) === "g00002", "the user is now shown in «Офис» (" + cs[1].count + "), not in «Дом» (" + cs[0].count + ")");
  ok(await act(page, userDots("anna_phone"), "Убрать из группы"), "«Убрать из группы» is offered");
  s = await sent(); await unfold(page, ""); cs = await cards(page);
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups.every(g => !g.members.includes("anna_phone")) && cs[3].rows.includes("anna_phone") && cs[3].count === "3 · 1 онлайн" && (await home("anna_phone")) === "", "«Убрать из группы» leaves the user in «Без группы» (" + cs[3].count + "): " + brief(s));
  m = await labelsOf(page, userDots("anna_phone"));
  ok(!m.includes("Убрать из группы") && m.filter(l => /^В группу/.test(l)).length === 3, "a user in no group is offered all three groups and no «Убрать из группы»: " + m.join());
  // delete: «Отмена» in the second tab, then for real
  await pageB.bringToFront(); await pageB.reload({ waitUntil: "networkidle0" }); await sleep(400); await unfold(pageB, "g00001");
  await act(pageB, userDots("max_phone"), "Удалить");
  s = await sent();
  ok(saidB.length === 1 && /max_phone/.test(saidB[0]) && s.length === 0 && !!(await row(pageB, "max_phone")), "«Удалить» asks first; «Отмена» sends nothing and keeps the user: «" + saidB[0] + "», sent: " + brief(s));
  await page.bringToFront(); said.length = 0;
  await act(page, userDots("anna_phone"), "Удалить"); await sleep(400);
  s = await sent(); ts = await toasts(page);
  ok(said.length === 1 && /Удалить пользователя «anna_phone»/.test(said[0]) && brief(s) === "DELETE /api/clients/anna_phone", "«Удалить» asks «" + said[0] + "» and sends one request: " + brief(s));
  ok((await row(page, "anna_phone")) === null && ts.includes("Пользователь удалён") && !(await cl("anna_phone")) && (await card(page, "")).count === "2", "the deleted user is gone from the list and from the server: notes " + J(ts) + ", «Без группы» " + (await card(page, "")).count);

  // ── 5. the menu of a group ──
  await restart(page); await unfold(page, "g00001");
  m = await labelsOf(page, groupDots("g00001"));
  ok(m.join() === "Срок для всех,Пригласить,Переименовать,Сделать пространством,Фильтрация,Без чужого DNS,Выход,Приостановить всех,Отозвать доступ,Удалить группу", "the ⋮ of «Дом» offers: " + m.join());
  const four = ["anna_phone", "tv_gostinaya", "olga_ipad", "max_phone"];
  const rowsOf = async names => { const out = []; for (const n of names) out.push(await row(page, n)); return out; };
  ok(await act(page, groupDots("g00001"), "Срок для всех", "30 дней"), "«Срок для всех → 30 дней» is offered");
  s = await sent(); const d30 = await ddmm(page, 30); let rs = await rowsOf(four); ts = await toasts(page);
  ok(brief(s) === "POST /api/clients/expire" && J(s[0].body) === J({ names: four, expires: "30d" }), "«Срок для всех» sends one request with the four users of the group: " + brief(s) + " " + J(s[0] && s[0].body));
  ok(rs.every(x => x.badge === "до " + d30) && ts.includes("Дом: до " + d30), "every user of the group gets the badge «до " + d30 + "»: " + J(rs.map(x => x.badge)));
  await act(page, groupDots("g00001"), "Срок для всех", "Без срока");
  s = await sent(); rs = await rowsOf(four);
  ok(brief(s) === "POST /api/clients/expire" && s[0].body.expires === "" && rs.every(x => x.badge === ""), "«Срок для всех → Без срока» clears all four: " + J(rs.map(x => x.badge)));
  // rename
  await page.click(groupDots("g00001")); await sleep(250); await pick(page, "Переименовать");
  const ren = await page.evaluate(() => { const i = document.querySelector('section[data-g="g00001"] input.rename'); return i ? { v: i.value, focus: document.activeElement === i } : null; });
  ok(ren && ren.v === "Дом" && ren.focus, "«Переименовать» turns the name into a field with the old name selected: " + J(ren));
  await page.keyboard.type("Семья"); await page.keyboard.press("Enter"); await idle(page);
  s = await sent(); c3 = await card(page, "g00001");
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].name === "Семья" && s[0].body.groups[0].id === "g00001" && s[0].body.groups[0].members.join() === four.join() && c3.name === "Семья" && c3.open,
    "the new name is saved for the same group with its users, and shown: " + brief(s) + " " + J(s[0] && s[0].body.groups[0]) + ", shown «" + c3.name + "»");
  await page.click(groupDots("g00001")); await sleep(250); await pick(page, "Переименовать"); await page.keyboard.type("Не надо"); await page.keyboard.press("Escape"); await sleep(400);
  ok((await sent()).length === 0 && (await card(page, "g00001")).name === "Семья", "Escape in the name field keeps the old name and sends nothing: «" + (await card(page, "g00001")).name + "»");
  // space
  ok(await act(page, groupDots("g00001"), "Сделать пространством"), "«Сделать пространством» is offered");
  s = await sent(); c3 = await card(page, "g00001"); m = await labelsOf(page, groupDots("g00001"));
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].space === true && c3.space === "true" && m.includes("Убрать пространство") && (await groupsNow())[0].space === true, "the group becomes a space (saved, the «Пространство» button is pressed, the menu now says «Убрать пространство»): " + brief(s) + ", pressed=" + c3.space);
  await act(page, groupDots("g00001"), "Убрать пространство");
  s = await sent(); c3 = await card(page, "g00001");
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].space === false && c3.space === "false" && (await groupsNow())[0].space === false, "«Убрать пространство» turns it back: " + brief(s) + ", pressed=" + c3.space);
  ok((await labelsOf(page, groupDots("g00003"))).includes("Убрать пространство"), "«Дача», a space from the start, is offered «Убрать пространство»");
  // filtering: «Фильтрация» opens a choice of five, the mode in force is marked
  const subs = p => p.evaluate(() => [...document.querySelectorAll("#menu button")].filter(b => b.querySelector(".sub")).map(b => b.firstChild.textContent + ":" + b.querySelector(".sub").textContent));
  await page.click(groupDots("g00001")); await sleep(250);
  ok((await subs(page)).includes("Фильтрация:обычная"), "«Фильтрация» shows the mode in force: " + J(await subs(page)));
  await pick(page, "Фильтрация"); await sleep(250);
  m = await menu(page); let marks = await subs(page);
  ok(m.join() === "Обычная,Pro,Pro++,Ultimate,Детская" && J(marks) === J(["Обычная:сейчас"]), "«Фильтрация» offers «Обычная», «Pro», «Pro++», «Ultimate», «Детская», the one in force marked: " + m.join() + " " + J(marks));
  await pick(page, "Детская"); await idle(page);
  s = await sent(); c3 = await card(page, "g00001"); ts = await toasts(page);
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].filter === "kids" && !("kids_lists" in s[0].body.groups[0]) && c3.badges.join() === "детский режим" && ts.includes("Семья: фильтрация — детская"),
    "the children's mode is saved (the page sends no lists of its own), shown as a badge and told: " + brief(s) + " " + J(s[0] && s[0].body.groups[0]) + " " + J(c3.badges) + " " + J(ts));
  ok(J((await groupsNow())[0].kids_lists) === J(["nsfw", "gambling", "bypass"]), "a group just switched to the children's mode gets NSFW, gambling and the bypass list, not social networks: " + J((await groupsNow())[0].kids_lists));
  m = await labelsOf(page, groupDots("g00001"));
  ok(m.join() === "Срок для всех,Пригласить,Переименовать,Сделать пространством,Фильтрация,Списки детского режима,Без чужого DNS,Выход,Приостановить всех,Отозвать доступ,Удалить группу",
    "a group in the children's mode is also offered «Списки детского режима»: " + m.join());
  await page.click(groupDots("g00001")); await sleep(250); await pick(page, "Списки детского режима"); await sleep(250);
  m = await menu(page); marks = await subs(page);
  ok(m.join() === "NSFW,Азартные игры,Обход фильтра,Соцсети" && J(marks) === J(["NSFW:вкл", "Азартные игры:вкл", "Обход фильтра:вкл"]), "«Списки детского режима» offers the four lists, those on marked: " + m.join() + " " + J(marks));
  await pick(page, "Соцсети"); await idle(page);
  s = await sent(); ts = await toasts(page);
  ok(brief(s) === "POST /api/ugroups" && J(s[0].body.groups[0].kids_lists) === J(["nsfw", "gambling", "bypass", "social"]) && J((await groupsNow())[0].kids_lists) === J(["nsfw", "gambling", "bypass", "social"]) && ts.includes("Семья: Соцсети — блокируются"),
    "a click on «Соцсети» switches it on and saves at once: " + brief(s) + " " + J(s[0] && s[0].body.groups[0].kids_lists) + " " + J(ts));
  ok(await act(page, groupDots("g00001"), "Списки детского режима", "NSFW"), "«Списки детского режима → NSFW» is offered");
  s = await sent(); ts = await toasts(page);
  ok(brief(s) === "POST /api/ugroups" && J(s[0].body.groups[0].kids_lists) === J(["gambling", "bypass", "social"]) && ts.includes("Семья: NSFW — не блокируются"), "a click on a list that is on switches it off: " + J(s[0] && s[0].body.groups[0].kids_lists) + " " + J(ts));
  ok(J(s[0].body.groups[1]) === J({ id: "g00002", name: "Офис", space: false, exit: null, filter: "std", no_doh: false, members: ["ivan_laptop", "petr_phone", "printer_office"] }), "the other groups are sent as before, without lists (and «Без чужого DNS» off): " + J(s[0] && s[0].body.groups[1]));
  ok(await act(page, groupDots("g00001"), "Фильтрация", "Pro++"), "«Фильтрация → Pro++» is offered");
  s = await sent(); c3 = await card(page, "g00001"); ts = await toasts(page);
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].filter === "proplus" && c3.badges.join() === "Pro++" && ts.includes("Семья: фильтрация — Pro++") && (await groupsNow())[0].filter === "proplus" && !("kids_lists" in (await groupsNow())[0]),
    "the level Pro++ is saved, shown as a badge and told; the lists of the children's mode are gone: " + brief(s) + " " + J(c3.badges) + " " + J(ts));
  await page.click(groupDots("g00001")); await sleep(250);
  m = await menu(page); marks = await subs(page);
  ok(!m.includes("Списки детского режима") && marks.includes("Фильтрация:Pro++"), "a group of a level is not offered the children's lists; «Фильтрация» says Pro++: " + m.join() + " " + J(marks));
  await pick(page, "Фильтрация"); await sleep(250);
  ok(J(await subs(page)) === J(["Pro++:сейчас"]), "…and Pro++ is the one marked: " + J(await subs(page)));
  await page.keyboard.press("Escape"); await sleep(150);
  ok(await act(page, groupDots("g00001"), "Фильтрация", "Pro++") && (await sent()).length === 0, "choosing the mode in force sends nothing");
  await act(page, groupDots("g00001"), "Фильтрация", "Обычная");
  s = await sent(); c3 = await card(page, "g00001"); ts = await toasts(page);
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].filter === "std" && c3.badges.length === 0 && (await groupsNow())[0].filter === "std" && ts.includes("Семья: фильтрация — обычная"), "«Фильтрация → Обычная» removes it: " + brief(s) + " " + J(c3.badges) + " " + J(ts));
  // «Без чужого DNS»: switched by a click, saved at once, marked in the menu and on the card
  ok(await act(page, groupDots("g00001"), "Без чужого DNS"), "«Без чужого DNS» is offered");
  s = await sent(); c3 = await card(page, "g00001"); ts = await toasts(page);
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].no_doh === true && s[0].body.groups[1].no_doh === false && (await groupsNow())[0].no_doh === true
     && c3.badges.join() === "без чужого DNS" && ts.some(t => t.startsWith("Семья: без чужого DNS — вкл. Эксперимент")),
    "a click switches it on: sent, kept, shown as a badge, told with the risk: " + brief(s) + " " + J(s[0] && s[0].body.groups[0]) + " " + J(c3.badges) + " " + J(ts));
  await page.click(groupDots("g00001")); await sleep(250);
  ok((await subs(page)).includes("Без чужого DNS:вкл"), "the menu marks it on: " + J(await subs(page)));
  await page.keyboard.press("Escape"); await sleep(150);
  await act(page, groupDots("g00001"), "Без чужого DNS");
  s = await sent(); c3 = await card(page, "g00001"); ts = await toasts(page);
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].no_doh === false && !("no_doh" in (await groupsNow())[0]) && c3.badges.length === 0 && ts.includes("Семья: без чужого DNS — выкл."),
    "a second click switches it off: " + brief(s) + " " + J(s[0] && s[0].body.groups[0]) + " " + J(c3.badges) + " " + J(ts));
  // exit
  await page.click(groupDots("g00001")); await sleep(250); await pick(page, "Выход");
  m = await menu(page);
  ok(m.join() === "Авто,exit1-nl,exit2-nl", "«Выход» offers «Авто» and the exits: " + m.join());
  await pick(page, "exit2-nl"); await idle(page);
  s = await sent(); c3 = await card(page, "g00001");
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].exit === 3 && c3.badges.join() === "exit2-nl", "the group is pinned to exit2-nl (saved, badge with the exit name): " + brief(s) + " exit=" + (s[0] && s[0].body.groups[0].exit) + " " + J(c3.badges));
  const allOn3 = await page.waitForFunction(() => [...document.querySelectorAll('section[data-g="g00001"] td.c-exit')].every(t => t.textContent === "exit2-nl"), { timeout: 9000 }).then(() => true, () => false);
  ok(allOn3, "its users are then shown on that exit: " + J((await rowsOf(four)).map(x => x.exit)));
  await act(page, groupDots("g00001"), "Выход", "Авто");
  s = await sent(); c3 = await card(page, "g00001");
  ok(brief(s) === "POST /api/ugroups" && s[0].body.groups[0].exit === null && c3.badges.length === 0 && (await groupsNow())[0].exit === null, "«Выход → Авто» unpins it and the badge goes: " + brief(s) + " " + J(c3.badges));
  // pause all
  ok(await act(page, groupDots("g00001"), "Приостановить всех"), "«Приостановить всех» is offered");
  s = await sent(); rs = await rowsOf(four); m = await labelsOf(page, groupDots("g00001"));
  ok(brief(s) === "POST /api/clients/suspend" && J(s[0].body) === J({ names: four, on: true }) && rs.every(x => x.st === "пауза") && m.includes("Возобновить всех"), "all four are paused with one request and shown as «пауза»: " + brief(s) + " " + J(rs.map(x => x.st)));
  await act(page, groupDots("g00001"), "Возобновить всех");
  s = await sent(); rs = await rowsOf(four);
  ok(brief(s) === "POST /api/clients/suspend" && s[0].body.on === false && rs.every(x => x.st !== "пауза"), "«Возобновить всех» resumes them: " + brief(s) + " " + J(rs.map(x => x.st)));
  // revoke: «Отмена» in the second tab, then for real
  await pageB.bringToFront(); await pageB.reload({ waitUntil: "networkidle0" }); await sleep(400); saidB.length = 0;
  await act(pageB, groupDots("g00002"), "Отозвать доступ");
  s = await sent();
  ok(saidB.length === 1 && s.length === 0 && (await card(pageB, "g00002")).count === "3 · 1 онлайн", "«Отозвать доступ» asks first; «Отмена» sends nothing: sent " + brief(s) + ", «Офис» " + (await card(pageB, "g00002")).count);
  await page.bringToFront(); said.length = 0; await unfold(page, "g00002");
  await act(page, groupDots("g00002"), "Отозвать доступ"); await sleep(400);
  s = await sent(); ts = await toasts(page);
  ok(said.length === 1 && /«Офис»/.test(said[0]) && /3 пользователя будут удалены/.test(said[0]), "the question names the group and the number: «" + said[0] + "»");
  ok(brief(s) === "POST /api/clients/remove" && J(s[0].body) === J({ names: ["ivan_laptop", "petr_phone", "printer_office"] }), "«Отозвать доступ» sends one request with exactly the users of «Офис»: " + brief(s) + " " + J(s[0] && s[0].body));
  c3 = await card(page, "g00002"); let st = await srv("/api/clients?limit=100"), gs = await groupsNow();
  ok(c3 && !c3.hidden && c3.count === "" && c3.rows.length === 0 && ts.includes("Доступ отозван: Офис"), "the users disappear, the group stays, empty: " + J(c3 && [c3.name, c3.count, c3.rows]) + ", notes " + J(ts));
  ok(st.clients.length === 8 && !st.clients.some(c => /ivan|petr|printer/.test(c.name)) && gs.length === 3 && gs[1].id === "g00002" && gs[1].members.length === 0, "on the server the three users are gone and the group is still there: " + st.clients.length + " users, groups " + J(gs.map(g => [g.name, g.members.length])));
  m = await labelsOf(page, groupDots("g00002"));
  ok(m.join() === "Пригласить,Переименовать,Сделать пространством,Фильтрация,Без чужого DNS,Выход,Удалить группу", "an empty group is not offered what needs users: " + m.join());
  // delete a group
  said.length = 0; await act(page, groupDots("g00002"), "Удалить группу");
  s = await sent(); cs = await cards(page);
  ok(said.length === 0 && brief(s) === "POST /api/ugroups" && s[0].body.groups.map(g => g.id).join() === "g00001,g00003" && cs.map(c => c.name).join() === "Семья,Дача,Без группы", "an empty group is deleted without a question: " + brief(s) + ", left " + cs.map(c => c.name).join());
  await act(page, groupDots("g00003"), "Удалить группу");
  s = await sent(); await unfold(page, ""); cs = await cards(page); st = await srv("/api/clients?limit=100");
  ok(said.length === 1 && /Удалить группу «Дача»/.test(said[0]) && /останутся без группы/.test(said[0]), "deleting a group with users asks: «" + said[0] + "»");
  ok(brief(s) === "POST /api/ugroups" && cs.map(c => c.name).join() === "Семья,Без группы" && ["router_dacha", "tv_dacha"].every(n => cs[1].rows.includes(n)) && st.clients.length === 8, "the group is gone, its users stay and are now in «Без группы»: " + brief(s) + " " + J(cs[1].rows));

  // ── 6. «Новая группа» ──
  await page.click("#ugAdd"); await sleep(300);
  ok(await page.evaluate(() => { const c = document.querySelector("#userBlocks .new-card"); return !!c && c === document.getElementById("userBlocks").firstElementChild && document.activeElement === c.querySelector("input"); }), "«Новая группа» opens a name card at the top with the cursor in it");
  await page.evaluate(() => [...document.querySelectorAll("#userBlocks .new-card button")].find(b => b.textContent === "Создать").click()); await sleep(300);
  ok((await sent()).length === 0 && !!(await page.$("#userBlocks .new-card")), "«Создать» with an empty name sends nothing");
  await page.keyboard.type("Гости"); await page.keyboard.press("Enter"); await idle(page);
  s = await sent(); cs = await cards(page); gs = await groupsNow();
  ok(brief(s) === "POST /api/ugroups" && J(s[0].body.groups[0]) === J({ id: null, name: "Гости", space: false, exit: null, filter: "std", no_doh: false, members: [] }) && s[0].body.groups.length === 2, "a name and Enter save a new empty group in front of the others: " + brief(s) + " " + J(s[0] && s[0].body.groups[0]));
  ok(cs.map(c => c.name).join() === "Гости,Семья,Без группы" && cs[0].count === "" && !(await page.$("#userBlocks .new-card")) && gs[0].name === "Гости" && /^g\d+$/.test(gs[0].id) && cs[0].key === gs[0].id, "the new group is shown first, empty, under the id the server gave it: " + cs.map(c => c.name).join() + " " + J(gs[0]));
  await page.click("#ugAdd"); await sleep(300); await page.keyboard.type("Пробная"); await page.keyboard.press("Escape"); await sleep(300);
  ok(!(await page.$("#userBlocks .new-card")) && (await sent()).length === 0, "Escape in the name card cancels, nothing is sent");
  await page.click("#ugAdd"); await sleep(300); await page.keyboard.type("гости"); await page.keyboard.press("Enter"); await idle(page);
  s = await sent(); ts = await toasts(page); cs = await cards(page);
  ok(brief(s) === "POST /api/ugroups" && ts.some(t => /уже есть/.test(t)) && cs.map(c => c.name).join() === "Гости,Семья,Без группы" && (await groupsNow()).length === 2, "a second group with the same name is refused by the server and the list stays as it is: notes " + J(ts) + ", shown " + cs.map(c => c.name).join());

  // ── 7. a save from a tab with an older list ──
  await restart(page, pageB);
  await pageB.bringToFront(); await unfold(pageB, "g00001");
  await pageB.click(userDots("anna_phone")); await sleep(300);              // the open menu keeps this tab on the list it has
  await page.bringToFront(); await unfold(page, "g00001");
  await act(page, userDots("tv_gostinaya"), "В группу «Офис»");
  s = await sent(); const vA = (await srv("/api/ugroups")).v;
  ok(brief(s) === "POST /api/ugroups" && (await home("tv_gostinaya")) === "g00002", "tab one moves tv_gostinaya to «Офис»: " + brief(s));
  await pageB.bringToFront(); await sleep(300);
  ok((await menu(pageB)).includes("В группу «Дача»"), "tab two still has its menu open on the older list: " + (await menu(pageB)).join());
  await pick(pageB, "В группу «Дача»"); await idle(pageB);
  s = await sent(); ts = await toasts(pageB);
  ok(brief(s) === "POST /api/ugroups" && s[0].body.base !== vA && inGroup(s[0].body, "g00001").includes("tv_gostinaya"), "tab two sends its save based on the older list (base " + (s[0] && s[0].body.base) + ", the server has " + vA + ")");
  ok(ts.some(t => /группы изменились в другом окне/.test(t)), "tab two is told «группы изменились в другом окне»: " + J(ts));
  await unfold(pageB, "g00002"); cs = await cards(pageB);
  ok(cs[1].rows.includes("tv_gostinaya") && !cs[0].rows.includes("tv_gostinaya") && cs[0].rows.includes("anna_phone") && !cs[2].rows.includes("anna_phone") && cs[0].count === "3 · 2 онлайн" && cs[1].count === "4 · 2 онлайн",
    "tab two now shows the list as the server has it (tv_gostinaya in «Офис», anna_phone still in «Дом»): " + J(cs.map(c => [c.name, c.count, c.rows])));
  gs = await groupsNow();
  ok((await srv("/api/ugroups")).v === vA && gs[1].members.includes("tv_gostinaya") && gs[0].members.join() === "anna_phone,olga_ipad,max_phone" && gs.reduce((a, g) => a + g.members.length, 0) === 9, "nothing is lost on the server: the first tab's change is there, the refused one is not: " + J(gs.map(g => [g.name, g.members])));
  ok(!(await pageB.$eval("#page", e => e.classList.contains("busy"))), "tab two is not left waiting");
  await act(pageB, userDots("anna_phone"), "В группу «Дача»");
  s = await sent(); gs = await groupsNow();
  ok(brief(s) === "POST /api/ugroups" && s[0].body.base === vA && gs[2].members.includes("anna_phone") && gs[1].members.includes("tv_gostinaya"), "the same action repeated in tab two goes through on top of the new list, keeping the first tab's change: " + J(gs.map(g => [g.name, g.members])));
  await page.bringToFront(); await unfold(page, "g00003");
  const seen = await page.waitForFunction(() => { const t = [...document.querySelectorAll("#userBlocks tbody tr")].find(t => (t.querySelector(".uname") || {}).textContent === "anna_phone"); return !!t && t.closest("section").dataset.g === "g00003"; }, { timeout: 12000 }).then(() => true, () => false);
  ok(seen, "tab one, left alone, shows the other tab's change by itself within seconds: anna_phone is in " + J((await row(page, "anna_phone") || {}).group));
  ok((await sent()).length === 0, "picking up the other tab's change sends nothing");

  // ── 8. the server refuses a save ──
  await restart(page); await unfold(page, "g00001");
  await ctl({ fail: "/api/ugroups" });
  const n0 = (await apiLog()).length;
  await act(page, userDots("olga_ipad"), "В группу «Офис»");
  s = await sent(); ts = await toasts(page); cs = await cards(page); gs = await groupsNow(); log = (await apiLog()).slice(n0);
  ok(brief(s) === "POST /api/ugroups" && ts.some(t => /сервер отказал/.test(t)), "a refused save shows the server's words: " + J(ts));
  ok(cs[0].rows.join() === gs[0].members.join() && cs[0].count === "4 · 3 онлайн" && cs[1].count === "3 · 1 онлайн" && gs[0].members.includes("olga_ipad") && !(await page.$eval("#page", e => e.classList.contains("busy"))),
    "after the refusal the page shows what the server has (olga_ipad still in «Дом») and is not left waiting: " + J(cs.map(c => [c.name, c.count])));
  const iPost = log.findIndex(x => x.method === "POST" && x.path === "/api/ugroups");
  ok(iPost >= 0 && log.slice(iPost + 1).some(x => x.method === "GET" && x.path === "/api/ugroups"), "after the refusal the page reads the list again");
  await act(page, userDots("olga_ipad"), "В группу «Офис»");
  s = await sent();
  ok(brief(s) === "POST /api/ugroups" && (await home("olga_ipad")) === "g00002" && (await card(page, "g00002")).count === "4 · 2 онлайн", "the same action repeated goes through: " + brief(s) + ", «Офис» " + (await card(page, "g00002")).count);
  await ctl({ fail: "/api/clients/suspend" });
  await act(page, userDots("tv_gostinaya"), "Приостановить");
  s = await sent(); ts = await toasts(page); r = await row(page, "tv_gostinaya");
  ok(brief(s) === "POST /api/clients/suspend" && ts.some(t => /сервер отказал/.test(t)) && r.st !== "пауза" && !(await page.$eval("#page", e => e.classList.contains("busy"))), "a refused «Приостановить» shows the error and does not pretend the user is paused: status «" + r.st + "», notes " + J(ts));

  // ── 9. phone width ──
  await restart(page);
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 }); await page.reload({ waitUntil: "networkidle0" }); await sleep(500);
  const fits = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  const names = () => page.evaluate(() => [...document.querySelectorAll("#userBlocks section[data-g]:not([hidden]) .fold span")].map(e => ({ t: e.textContent, w: Math.round(e.getBoundingClientRect().width), cut: e.scrollWidth > e.clientWidth })));
  let nm = await names();
  ok(await fits(), "390px: the folded list does not scroll sideways");
  ok(nm.length === 4 && nm.every(x => !x.cut && x.w >= 24), "390px: every group name is shown in full: " + J(nm));
  cs = await cards(page);
  ok(cs.map(c => c.count).join("|") === "4 · 3 онлайн|3 · 1 онлайн|2 · 1 онлайн|2", "390px: the counters are still shown: " + cs.map(c => c.count).join(" | "));
  const was = (await card(page, "g00001")).open;                 // what is unfolded is remembered from the part above
  pt = await headPoint(page, "g00001", ".group-count"); await page.mouse.click(pt.x, pt.y); await sleep(300);
  ok((await card(page, "g00001")).open === !was, "390px: a tap on the header line (on the counter) folds and unfolds the group: was open=" + was + ", now " + (await card(page, "g00001")).open);
  await unfold(page, "g00001");
  const narrow = await page.evaluate(() => {
    const tr = document.querySelector('section[data-g="g00001"] tbody tr'), n = tr.querySelector(".uname").getBoundingClientRect(), box = b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.left >= 0 && r.right <= window.innerWidth; };
    return { open: document.querySelector('section[data-g="g00001"]').classList.contains("open"), nameW: Math.round(n.width), cut: tr.querySelector(".uname").scrollWidth > tr.querySelector(".uname").clientWidth, st: (tr.querySelector(".c-narrow") || {}).textContent, stShown: !!tr.querySelector(".c-narrow") && tr.querySelector(".c-narrow").getBoundingClientRect().height > 0,
      code: box(tr.querySelector('[data-k$=":code"]')), more: box(tr.querySelector('[data-k$=":more"]')) };
  });
  ok(narrow.open && (await fits()), "390px: with a group unfolded nothing scrolls sideways");
  ok(narrow.nameW >= 80 && !narrow.cut && narrow.stShown && /онлайн/.test(narrow.st) && narrow.code && narrow.more, "390px: a row shows the whole user name with the status under it, «Код» and ⋮ are on the screen: " + J(narrow));
  await page.click(userDots("anna_phone")); await sleep(300);
  const mb = await page.$eval("#menu", e => { const r = e.getBoundingClientRect(); return { hidden: e.hidden, l: Math.round(r.left), r: Math.round(r.right) }; });
  ok(!mb.hidden && mb.l >= 0 && mb.r <= 390, "390px: the user menu opens inside the screen: " + J(mb));
  await page.keyboard.press("Escape"); await sleep(200);
  await page.click('section[data-g="g00001"] .group-head button[aria-label^="Добавить в"]'); await sleep(300);
  const fld = await page.$eval("#ugAddInput", e => { const r = e.getBoundingClientRect(); return { l: Math.round(r.left), r: Math.round(r.right), w: Math.round(r.width) }; });
  ok((await fits()) && fld.l >= 0 && fld.r <= 390 && fld.w >= 120, "390px: the name field of «+» fits the screen: " + J(fld));
  await page.focus("#ugAddInput"); await page.keyboard.press("Escape"); await sleep(200);
  await page.click('[data-k="anna_phone:code"]'); await sleep(600);
  const pb = await page.$eval("#codePanel", e => { const r = e.getBoundingClientRect(); return { hidden: e.hidden, l: Math.round(r.left), r: Math.round(r.right) }; });
  ok(!pb.hidden && pb.l >= 0 && pb.r <= 390 && (await fits()), "390px: the code panel fits the screen, nothing scrolls sideways: " + J(pb));
  await page.click('#codePanel button[aria-label="Закрыть"]'); await sleep(200);
  // a crowded header: pinned to an exit and in the children's mode at once
  gs = await groupsNow(); gs[0].exit = 3; gs[0].filter = "kids"; gs[1].name = "Бухгалтерия"; gs[1].filter = "ultimate"; gs[1].exit = 1;
  await ctl({ set: { ugroups: gs } }); await page.reload({ waitUntil: "networkidle0" }); await sleep(500);
  nm = await names(); cs = await cards(page);
  ok(cs[0].badges.join() === "exit2-nl,детский режим" && (await fits()), "390px: a group with both badges (exit and children's mode) does not make the page scroll sideways: " + J(cs[0].badges));
  ok(cs[1].badges.join() === "exit1-nl,Ultimate" && (await fits()), "390px: nor does a group of a level: " + J(cs[1].badges));
  ok(nm.slice(0, 2).every(x => !x.cut), "390px: the names of groups that carry badges are still shown in full: " + J(nm.slice(0, 2)));
  ok((await sent()).length === 0, "nothing of the phone-width part changed the server");
  // ── the traffic history is not there (nothing collected yet): the counters since the tunnel started, as before ──
  await restart(); await ctl({ set: { traffic_empty: true } });
  const pageC = await open(browser, "users");
  cs = await cards(pageC);
  ok(cs.map(c => c.traf).join("|") === "122 ГБ|20,4 ГБ|56,5 ГБ|296 МБ" && cs.every(c => c.trafTitle === "Трафик с запуска туннеля"), "without a history a group shows the traffic since the tunnel started, and its tooltip says so: " + cs.map(c => c.traf).join(" | ") + " — " + cs[0].trafTitle);
  await pageC.click('#userBlocks section[data-g="g00003"] .fold'); await sleep(400);
  r = await row(pageC, "router_dacha");
  ok(r && r.traf === "44,7 ГБ" && r.trafTitle === "Трафик с запуска туннеля", "…and so does a row: " + J(r));
  // counted in this tab (the other two tabs of this file keep polling on their own)
  const reqs = () => pageC.evaluate(() => { const n = re => performance.getEntriesByType("resource").filter(e => re.test(e.name)).length; return { traffic: n(/\/api\/traffic/), state: n(/\/api\/state/) }; });
  const q0 = await reqs(); await sleep(11000); const q1 = await reqs();
  ok(q0.traffic === 1 && q1.traffic === 1 && q1.state >= q0.state + 2, "the users page asks for the history once, when it opens, and not with the state polls after that: " + J([q0, q1]));
  // a minute later the history is there (and has grown): the next poll asks once more, headers and open rows follow
  await ctl({ set: { traffic_empty: false, traffic_x: 2 } });
  await pageC.click('#userBlocks section[data-g=""] .fold'); await sleep(400);
  await pageC.evaluate(() => { const real = Date.now.bind(Date); Date.now = () => real() + 61000; });
  await sleep(6500);
  cs = await cards(pageC); r = await row(pageC, "router_dacha"); const q2 = await reqs();
  ok(q2.traffic === 2 && cs.map(c => c.traf).join("|") === "40,4 ГБ|6,7 ГБ|18,7 ГБ|97,6 МБ" && cs.every(c => c.trafTitle === "Трафик за 30 дней"), "after a minute the history is asked for once more and the group headers show it: " + cs.map(c => c.traf).join(" | ") + " — " + cs[0].trafTitle + " " + J(q2));
  ok(r && r.traf === "14,8 ГБ" && r.trafTitle === "Трафик за 30 дней", "…and so does a row that was already unfolded: " + J(r));
  r = await row(pageC, "new_phone");
  ok(r && r.traf === "—" && r.trafTitle === "Трафик за 30 дней", "a user without a byte in the loaded history shows a dash for the 30 days, not a since-start counter: " + J(r));
  // ── many users: the server counts them, pages them and searches them; the page never holds them all ──
  await restart(); await ctl({ many: 130 });
  const pageD = await open(browser, "users");
  cs = await cards(pageD);
  ok(cs[3].count === "132" && cs.slice(0, 3).map(c => c.count).join("|") === "4 · 3 онлайн|3 · 1 онлайн|2 · 1 онлайн", "the counts come from the server: «Без группы» " + cs[3].count);
  await pageD.click('#userBlocks section[data-g=""] .fold'); await sleep(600);
  const rowsIn = async key => (await card(pageD, key)).rows.length;
  const moreIn = key => pageD.evaluate(key => !!document.querySelector(`section[data-g="${key}"] .btn.more`), key);
  ok((await rowsIn("")) === 50 && (await moreIn("")), "an unfolded card shows the first 50 users and «Показать ещё»: " + (await rowsIn("")));
  await pageD.click('section[data-g=""] .btn.more'); await sleep(600);
  ok((await rowsIn("")) === 100 && (await moreIn("")), "«Показать ещё» brings 50 more: " + (await rowsIn("")));
  await pageD.click('section[data-g=""] .btn.more'); await sleep(600);
  ok((await rowsIn("")) === 132 && !(await moreIn("")), "…until all are there, then the button goes: " + (await rowsIn("")));
  const logBefore = (await apiLog()).length;
  await pageD.type("#userSearch", "bulk_012"); await sleep(600);
  const asked = (await apiLog()).slice(logBefore).filter(x => x.path === "/api/clients" && x.query.q);
  cs = await cards(pageD);
  ok(asked.length >= 1 && asked.every(x => x.query.q.length > 3) && asked[asked.length - 1].query.q === "bulk_012", "the search is asked of the server once the typing pauses, not for every letter: " + J(asked.map(x => x.query.q)));
  ok(cs[3].rows.join() === "bulk_0120,bulk_0121,bulk_0122,bulk_0123,bulk_0124,bulk_0125,bulk_0126,bulk_0127,bulk_0128,bulk_0129" && cs.slice(0, 3).every(c => c.hidden), "it finds the ten whose names match: " + J(cs[3].rows));
  await finish(browser, [page, pageB, pageC, pageD]);
});
