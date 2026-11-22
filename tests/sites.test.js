// «Сайты»: «Через заграницу» (groups of addresses that go through an exit; changes are staged
// and sent with «Применить») and «Блокируется» (ready-made lists, «Опасные сайты», own groups;
// every change is sent at once).
const { BASE, sleep, ok, ctl, changes, apiLog, connect, open, menu, pick, toasts, finish, run } = require("./lib");
run(async () => {
  const browser = await connect();
  const page = await open(browser, "sites");
  const dialogs = []; page.on("dialog", d => dialogs.push(d.message()));

  // ── helpers ────────────────────────────────────────────────────────────────
  const J = x => JSON.stringify(x);
  const card = (name, grid) => `#${grid || "siteGrid"} section[aria-label="Группа ${name}"]`;
  const chipSel = (group, text, grid) => `${card(group, grid)} .chip[data-item="${text}"]`;
  // every card of a grid as plain data
  const snap = (p, grid) => p.evaluate(id => [...document.getElementById(id).children].map(c => {
    const q = s => c.querySelector(s), t = s => (q(s) ? q(s).textContent : null);
    return { label: c.getAttribute("aria-label"), cls: c.className, name: t(".group-name"), on: q(".switch") ? q(".switch").getAttribute("aria-checked") === "true" : null,
      count: t(".group-count"), day: t(".group-day"), meta: q(".group-meta") ? q(".group-meta").innerText.replace(/\n+/g, " | ") : null, linkIcon: !!q(".group-ic"),
      plus: !!q('.group-head button[aria-label^="Добавить в"]'), field: !!q("input.input.code"), text: c.textContent,
      chips: [...c.querySelectorAll(".chip")].map(x => ({ t: x.dataset.item || x.textContent, cls: x.className, star: !!x.querySelector("b.star"), sub: x.querySelector(".chip-sub") ? x.querySelector(".chip-sub").textContent : null })) };
  }), grid || "siteGrid");
  const find = (cards, name) => cards.find(c => c.name === name) || { chips: [], missing: true };
  const items = c => c.chips.filter(x => !/\bauto\b/.test(x.cls)).map(x => x.t);
  const chipOf = (c, t) => c.chips.find(x => x.t === t) || null;
  const has = (cls, word) => new RegExp("\\b" + word + "\\b").test(cls || "");
  // the bar «Отменить / Применить N»: null when it is not shown
  const bar = () => page.evaluate(() => { const a = document.getElementById("siteActions"); if (!a || a.hidden) return null; const c = a.querySelector("#siteApply .count"); return { label: a.querySelector("#siteApply span").textContent, n: c ? +c.textContent : 0 }; });
  const posts = async path => (await changes()).filter(x => x.path === path);
  const sitesNow = async () => (await fetch(BASE + "/api/sites")).json();
  const fill = async (p, sel, text) => {
    await p.$eval(sel, e => { e.value = ""; e.dispatchEvent(new Event("input", { bubbles: true })); });
    if (text) await p.type(sel, text);
    await sleep(200);
  };
  // type an entry in the field that is open in a card and press Enter; returns the message under the field
  const enter = async (p, id, text) => {
    await fill(p, "#" + id, text); await p.keyboard.press("Enter"); await sleep(300);
    return p.evaluate(i => { const e = document.getElementById(i + "Err"); return e ? e.textContent : null; }, id);
  };
  const waitToast = (p, rx) => p.waitForFunction(src => [...document.querySelectorAll(".toast")].some(t => new RegExp(src).test(t.textContent)), { timeout: 8000 }, rx).then(() => true, () => false);
  // press «Применить», wait until the page has the answer, and say whether a toast like rx is shown
  const apply = async (p, rx) => {
    await p.click("#siteApply");
    await p.waitForFunction(() => !document.getElementById("page").classList.contains("busy"), { timeout: 8000 }).catch(() => {});
    return waitToast(p, rx || "Применено|Сохранено");
  };
  const cardMenu = async (p, name, grid) => { await p.click(`${card(name, grid)} button[aria-label="Действия: ${name}"]`); await sleep(200); return menu(p); };
  const plusOf = async (p, name, grid) => { await p.click(`${card(name, grid)} button[aria-label="Добавить в «${name}»"]`); await sleep(200); };

  // ── the mode: only the list, or everything foreign ─────────────────────────
  const modeNow = () => page.evaluate(() => [...document.querySelectorAll("#siteMode button")].map(b => [b.textContent, b.getAttribute("aria-pressed")].join(":")).join());
  ok((await modeNow()) === "По списку:true,Всё зарубежное:false", "the mode control shows two choices, «По списку» chosen: " + await modeNow());
  dialogs.length = 0;
  await page.click("#siteMode button:nth-child(1)"); await sleep(300);
  ok(dialogs.length === 0 && (await posts("/api/mode")).length === 0, "pressing the mode that is already on asks nothing and sends nothing");
  await page.click("#siteMode button:nth-child(2)");
  ok(await waitToast(page, "Применено"), "switching to «Всё зарубежное» is confirmed by a toast");
  let mp = await posts("/api/mode");
  ok(dialogs.join() === "Пускать через заграницу всё зарубежное?" && mp.length === 1 && mp[0].body.mode === "geo" && (await sitesNow()).mode === "geo", "the switch asks first and sends {mode: geo} once: " + J([dialogs, mp.map(x => x.body)]));
  ok((await modeNow()) === "По списку:false,Всё зарубежное:true", "…and the control shows «Всё зарубежное»: " + await modeNow());
  await ctl({ set: { mode_fail: true } }); dialogs.length = 0;
  await page.click("#siteMode button:nth-child(1)");
  ok(await waitToast(page, "возвращён прежний режим"), "a switch the server could not apply shows its error");
  ok((await modeNow()) === "По списку:false,Всё зарубежное:true", "…and the control stays on the mode that is running: " + await modeNow());
  await ctl({ set: { mode_fail: false } });
  await page.click("#siteMode button:nth-child(1)"); await sleep(600);
  ok((await modeNow()) === "По списку:true,Всё зарубежное:false" && (await sitesNow()).mode === "list" && dialogs.slice(-1)[0] === "Пускать через заграницу только сайты из списка?", "back to «По списку»: asked, sent, shown: " + J(dialogs));

  // ── the home country ───────────────────────────────────────────────────────
  const countryNow = () => page.evaluate(() => { const s = document.getElementById("siteCountry"); return s ? [s.value, s.selectedOptions[0].textContent] : null; });
  ok(J(await countryNow()) === J(["ru", "Россия"]), "the home country is shown next to the mode: Russia when the server names none: " + J(await countryNow()));
  dialogs.length = 0;
  await page.select("#siteCountry", "kz");
  await page.waitForFunction(() => document.getElementById("siteCountry").value === "kz" && !document.getElementById("siteCountry").disabled, { timeout: 8000 }).catch(() => {});
  let cp = await posts("/api/country");
  ok(dialogs.join() === "Своя страна — Казахстан? Её сети пойдут напрямую, список скачается заново." && cp.length === 1 && cp[0].body.country === "kz" && J(await countryNow()) === J(["kz", "Казахстан"]),
    "choosing another country asks first, sends {country: kz} once and shows it from the server's state: " + J([dialogs, cp.map(x => x.body), await countryNow()]));
  await page.select("#siteCountry", "ru");
  await page.waitForFunction(() => document.getElementById("siteCountry").value === "ru" && !document.getElementById("siteCountry").disabled, { timeout: 8000 }).catch(() => {});
  ok((await posts("/api/country")).length === 2 && J(await countryNow()) === J(["ru", "Россия"]), "…and back to Russia");

  // ── «Через заграницу»: what is shown ───────────────────────────────────────
  let v = await snap(page);
  ok(v.map(c => c.name).join() === "YouTube,Telegram,ИИ,Работа,Список друга", "one card per group, in the stored order: " + v.map(c => c.name).join());
  ok(items(find(v, "YouTube")).join() === "youtube.com,googlevideo.com,ytimg.com" && find(v, "YouTube").count === "3", "«YouTube» shows its three addresses as chips and the count 3: " + J(items(find(v, "YouTube"))) + " " + find(v, "YouTube").count);
  let c = find(v, "Telegram");
  ok(c.count === "19" && c.chips.some(x => has(x.cls, "auto") && /17 сетей/.test(x.t)), "the automatic group «Telegram» counts its 2 addresses + 17 own networks and shows them as one chip: " + c.count + " " + J(c.chips.map(x => x.t)));
  c = find(v, "ИИ");
  ok(chipOf(c, "*.perplexity.ai") && chipOf(c, "*.perplexity.ai").star, "a pattern is shown with its star marked: " + J(chipOf(c, "*.perplexity.ai")));
  c = find(v, "Работа");
  ok(c.on === false && has(c.cls, "off") && has((chipOf(c, "10.20.0.0/16") || {}).cls, "net"), "«Работа» is switched off and its network chip is marked as a network: on=" + c.on + " " + J(chipOf(c, "10.20.0.0/16")));
  c = find(v, "Список друга");
  ok(c.linkIcon && /^example\.org/.test(c.meta || "") && /сегодня|вчера/.test(c.meta || "") && c.count === "3" && c.chips.length === 0 && !c.plus,
    "a list by link shows its source, when it was read and its size, not its entries, and has no «+»: meta=" + c.meta + " count=" + c.count + " chips=" + c.chips.length + " plus=" + c.plus);
  ok((await bar()) === null, "nothing staged: no «Применить»/«Отменить» bar");

  // ── the switch, staging, «Отменить» ────────────────────────────────────────
  await page.click(card("Работа") + " .switch"); await sleep(200);
  v = await snap(page); let b = await bar();
  ok(find(v, "Работа").on === true && b && b.label === "Применить" && b.n === 1 && (await posts("/api/sites")).length === 0, "the switch of «Работа» is staged: on, «Применить 1», nothing sent: " + J(b));
  await page.click("#siteCancel"); await sleep(200);
  ok(find(await snap(page), "Работа").on === false && (await bar()) === null, "«Отменить» puts the switch back and hides the bar");

  await plusOf(page, "YouTube");
  let st = await page.evaluate(sel => { const f = document.getElementById("siteInput"), cd = document.querySelector(sel); return { inCard: !!f && cd.contains(f), focused: document.activeElement === f, pressed: cd.querySelector('button[aria-label^="Добавить в"]').getAttribute("aria-pressed"), fields: document.querySelectorAll("#siteGrid input.input.code").length }; }, card("YouTube"));
  ok(st.inCard && st.focused && st.pressed === "true" && st.fields === 1, "«+» of «YouTube» opens one field inside that card and puts the cursor in it: " + J(st));
  let err = await enter(page, "siteInput", "youtu.be");
  v = await snap(page); b = await bar(); c = find(v, "YouTube");
  ok(err === "" && has((chipOf(c, "youtu.be") || {}).cls, "new") && !has(chipOf(c, "youtube.com").cls, "new") && c.count === "4", "the added address is a chip marked new, the old chips are not: " + J(chipOf(c, "youtu.be")) + " count=" + c.count);
  ok(b && b.n === 1 && (await posts("/api/sites")).length === 0, "adding only stages: «Применить 1» and no request: " + J(b));
  ok(await page.evaluate(() => { const f = document.getElementById("siteInput"); return !!f && f.value === "" && document.activeElement === f; }), "the field stays open, empty and focused for the next address");

  // a wider network makes a narrower one redundant
  await fill(page, "#siteInput", "10.0.0.0/8");
  st = await page.evaluate(sel => ({ row: !document.getElementById("siteInputReplaceRow").hidden, text: document.getElementById("siteInputReplaceText").textContent, covered: document.querySelector(sel).classList.contains("covered") }), chipSel("Работа", "10.20.0.0/16"));
  ok(st.row && st.text === "Заменить 1 запись" && st.covered, "typing 10.0.0.0/8 offers «Заменить 1 запись» and strikes 10.20.0.0/16 out: " + J(st));
  await page.keyboard.press("Enter"); await sleep(300);
  v = await snap(page); b = await bar();
  ok(has((chipOf(find(v, "YouTube"), "10.0.0.0/8") || {}).cls, "net") && !chipOf(find(v, "Работа"), "10.20.0.0/16") && b && b.n === 3, "the network is staged as a network chip and replaces the narrower one («Применить 3»): " + J(b));
  await page.click("#siteCancel"); await sleep(200);
  v = await snap(page);
  ok((await bar()) === null && items(find(v, "YouTube")).join() === "youtube.com,googlevideo.com,ytimg.com" && !!chipOf(find(v, "Работа"), "10.20.0.0/16") && !v.some(x => x.field), "«Отменить» restores every group and closes the field: " + J(items(find(v, "YouTube"))));
  ok((await posts("/api/sites")).length === 0, "staging and cancelling sent nothing");

  // ── what may be typed ──────────────────────────────────────────────────────
  await plusOf(page, "ИИ");
  const typed = {};
  for (const t of ["cdn*.example.net", "192.168.50.0/24", "https://Example.ORG/some/page?x=1", "*.foo.com"]) typed[t] = await enter(page, "siteInput", t);
  v = await snap(page); c = find(v, "ИИ");
  ok(Object.values(typed).every(e => e === ""), "a pattern, a network, a pasted link and «*.name» are all accepted: " + J(typed));
  ok(chipOf(c, "cdn*.example.net") && chipOf(c, "cdn*.example.net").star && has(chipOf(c, "cdn*.example.net").cls, "new"), "the pattern cdn*.example.net is a new chip with the star marked: " + J(chipOf(c, "cdn*.example.net")));
  ok(chipOf(c, "192.168.50.0/24") && has(chipOf(c, "192.168.50.0/24").cls, "net") && has(chipOf(c, "192.168.50.0/24").cls, "new"), "the network 192.168.50.0/24 is a new chip marked as a network: " + J(chipOf(c, "192.168.50.0/24")));
  ok(!!chipOf(c, "example.org") && !!chipOf(c, "foo.com") && !chipOf(c, "*.foo.com"), "a pasted link is reduced to its domain and «*.foo.com» to foo.com (a domain covers its subdomains): " + J(items(c)));
  const before = items(c).length, refused = {};
  for (const t of ["not a domain!", "a.*.com", "300.1.1.1", "10.0.0.0/4"]) refused[t] = await enter(page, "siteInput", t);
  ok(refused["not a domain!"] === "не похоже на домен" && /^слишком широко/.test(refused["a.*.com"]) && refused["300.1.1.1"] === "такой сети не бывает" && refused["10.0.0.0/4"] === "такой сети не бывает", "garbage is refused with a message under the field: " + J(refused));
  ok(items(find(await snap(page), "ИИ")).length === before, "nothing refused was staged");
  await page.type("#siteInput", "x"); await sleep(100);
  ok((await page.$eval("#siteInputErr", e => e.textContent)) === "", "the message goes away as soon as the text is changed");
  err = await enter(page, "siteInput", "chatgpt.com");
  v = await snap(page);
  ok(err === "уже есть в этой группе" && items(find(v, "ИИ")).filter(t => t === "chatgpt.com").length === 1 && !!chipOf(find(v, "YouTube"), "youtube.com"),
    "an address this group already has is refused (another group may have it too: the first rule in the order decides): " + err);

  // ── «Применить» ────────────────────────────────────────────────────────────
  b = await bar();
  ok(b && b.n === 4, "four staged entries: «Применить 4»: " + J(b));
  let base = (await sitesNow()).v, n0 = (await posts("/api/sites")).length;
  let done = await apply(page, "^Применено$");
  let p = (await posts("/api/sites")).slice(n0), body = (p[0] || {}).body || { groups: [] };
  ok(done && p.length === 1 && p[0].method === "POST", "«Применить» sends ONE POST /api/sites and says «Применено»: " + p.length + " " + J(await toasts(page)));
  ok(body.base === base, "the request carries base = the version the page has: " + body.base + " vs " + base);
  ok(body.groups.map(g => g.id + ":" + g.name + ":" + g.on).join() === "a00001:YouTube:true,a00002:Telegram:true,a00003:ИИ:true,a00004:Работа:false,a00005:Список друга:true", "all five groups go with their ids, names and switches: " + body.groups.map(g => g.id + ":" + g.name + ":" + g.on).join());
  ok(J((body.groups[2] || {}).items) === J(["openai.com", "chatgpt.com", "anthropic.com", "claude.ai", "*.perplexity.ai", "cdn*.example.net", "192.168.50.0/24", "example.org", "foo.com"]) && J(body.groups[0].items) === J(["youtube.com", "googlevideo.com", "ytimg.com"]), "«ИИ» goes with its old and new entries, «YouTube» untouched: " + J((body.groups[2] || {}).items));
  ok(body.groups[4] && body.groups[4].url === "https://example.org/list.txt" && !("items" in body.groups[4]), "the list by link goes as a link, without entries: " + J(body.groups[4]));
  v = await snap(page);
  ok((await bar()) === null && !find(v, "ИИ").chips.some(x => has(x.cls, "new")) && find(v, "ИИ").count === "9", "after applying the bar is gone and no chip is marked new: count=" + find(v, "ИИ").count);

  // ── adding from «Проверка» a domain that sits in a switched-off group ──────
  await fill(page, "#siteSearch", "notion.so");
  st = await page.evaluate(() => { const pc = document.querySelector("#siteGrid .probe"); return pc ? { rows: pc.querySelector(".probe-rows").innerText.replace(/\s*\n+\s*/g, " | "), add: pc.querySelector("#probeAdd") ? pc.querySelector("#probeAdd").textContent : null } : null; });
  ok(st && /Сейчас идёт \| напрямую · в выключенной группе «Работа»/.test(st.rows) && st.add === null, "notion.so sits in the switched-off «Работа»: the card says so and does not offer to add it a second time: " + J(st));
  v = await snap(page); b = await bar();
  ok(!b, "…and nothing is staged by looking at it: bar=" + J(b));
  if (b) { await page.click("#siteCancel"); await sleep(200); }
  await fill(page, "#siteSearch", "");

  // ── removing a chip, renaming, deleting, «Новая группа» ────────────────────
  await page.click(chipSel("YouTube", "ytimg.com") + " .chip-t"); await sleep(200);
  ok(J(await menu(page)) === J(["Убрать"]), "a click on a chip offers «Убрать»: " + J(await menu(page)));
  await pick(page, "Убрать");
  v = await snap(page); b = await bar();
  ok(items(find(v, "YouTube")).join() === "youtube.com,googlevideo.com" && b && b.n === 1, "«Убрать» takes the chip away and stages it: " + J(items(find(v, "YouTube"))) + " " + J(b));
  // the cross of a chip exists only where there is a pointer that can hover (the page's CSS says so)
  const canHover = await page.evaluate(() => matchMedia("(hover:hover)").matches);
  await page.hover(chipSel("YouTube", "googlevideo.com")); await sleep(200);
  const cross = await page.$eval(chipSel("YouTube", "googlevideo.com") + " .chip-x", e => ({ shown: getComputedStyle(e).display !== "none", label: e.getAttribute("aria-label") }));
  if (canHover) { ok(cross.shown && cross.label === "Убрать: googlevideo.com", "under the pointer a chip shows its cross: " + J(cross)); await page.click(chipSel("YouTube", "googlevideo.com") + " .chip-x").catch(() => {}); }
  else { console.log("note: this browser has no hovering pointer, the cross is not shown; removing through the menu instead"); await page.click(chipSel("YouTube", "googlevideo.com") + " .chip-t"); await sleep(200); await pick(page, "Убрать"); }
  await sleep(200);
  v = await snap(page); b = await bar();
  ok(items(find(v, "YouTube")).join() === "youtube.com" && b && b.n === 2, "the second removal is staged too: " + J(items(find(v, "YouTube"))) + " " + J(b));
  ok(J(await cardMenu(page, "Telegram")) === J(["Переименовать"]), "the automatic group can be renamed but has no «Удалить»: " + J(await menu(page)));
  await page.keyboard.press("Escape"); await sleep(150);
  ok(J(await cardMenu(page, "Работа")) === J(["Переименовать", "Удалить"]), "an ordinary group offers «Переименовать» and «Удалить»: " + J(await menu(page)));
  await pick(page, "Переименовать");
  ok(await page.evaluate(() => { const a = document.activeElement; return a.classList.contains("rename") && a.value === "Работа"; }), "«Переименовать» turns the title into a field with the old name");
  await page.keyboard.type("Работа и учёба"); await page.keyboard.press("Enter"); await sleep(250);
  v = await snap(page); b = await bar();
  ok(!find(v, "Работа и учёба").missing && find(v, "Работа").missing && b && b.n === 3, "the new name is staged: " + v.map(x => x.name).join() + " " + J(b));
  dialogs.length = 0;
  await cardMenu(page, "Работа и учёба"); await pick(page, "Удалить");
  v = await snap(page);
  ok(dialogs.join() === "Удалить группу «Работа и учёба» и её записи (3)?" && find(v, "Работа и учёба").missing, "deleting a group with entries asks first, then the card is gone: " + J(dialogs));
  await page.evaluate(() => [...document.querySelectorAll(".toolbar .tools button")].find(x => x.textContent === "Новая группа").click()); await sleep(200);
  ok(await page.evaluate(() => { const nc = document.querySelector("#siteGrid .new-card"); return !!nc && nc === document.getElementById("siteGrid").firstElementChild && document.activeElement === nc.querySelector("input"); }), "«Новая группа» opens a name card at the top with the cursor in it");
  await page.keyboard.type("Музыка"); await page.keyboard.press("Enter"); await sleep(250);
  v = await snap(page);
  ok(v[0].name === "Музыка" && v[0].field && v[0].on === true && v[0].chips.length === 0, "the new group is the first card, switched on, with its field open: " + J(v[0] && { name: v[0].name, field: v[0].field, on: v[0].on }));
  err = await enter(page, "siteInput", "spotify.com");
  v = await snap(page); b = await bar();
  ok(err === "" && has((chipOf(v[0], "spotify.com") || {}).cls, "new") && b && b.n === 8, "an address goes straight into the new group; the bar counts every staged change (2 removed + deleted group and its 3 entries + new group and its entry = 8): " + J(b));
  base = (await sitesNow()).v; n0 = (await posts("/api/sites")).length;
  done = await apply(page, "^Применено$");
  p = (await posts("/api/sites")).slice(n0); body = (p[0] || {}).body || { groups: [] };
  ok(done && p.length === 1 && body.base === base, "one POST with the current version for all of it: " + p.length + " base " + body.base + " vs " + base);
  ok(J(body.groups.map(g => [g.id, g.name, g.items || g.url])) === J([[null, "Музыка", ["spotify.com"]], ["a00001", "YouTube", ["youtube.com"]], ["a00002", "Telegram", ["telegram.org", "t.me"]], ["a00003", "ИИ", body.groups[3] && body.groups[3].items], ["a00005", "Список друга", "https://example.org/list.txt"]]),
    "the new group goes without an id, «YouTube» with one entry, the deleted group not at all: " + J(body.groups.map(g => [g.id, g.name, (g.items || []).length])));
  let srv = await sitesNow();
  ok(srv.groups.map(g => g.name).join() === "Музыка,YouTube,Telegram,ИИ,Список друга" && (await bar()) === null && (await snap(page)).map(x => x.name).join() === "Музыка,YouTube,Telegram,ИИ,Список друга", "the server and the page now have the same five groups: " + srv.groups.map(g => g.name).join());

  // ── «Список по ссылке» ─────────────────────────────────────────────────────
  ok(find(await snap(page), "Музыка").field, "after «Применить» the field of the group being filled stays open");
  await plusOf(page, "Музыка");
  ok(!(await snap(page)).some(x => x.field), "a second press on «+» closes the field");
  await page.click("#siteLinkBtn"); await sleep(200);
  ok(await page.evaluate(() => !!document.querySelector("#siteLinkSlot .linkform #siteLinkName") && !!document.getElementById("siteLinkUrl") && document.getElementById("siteLinkBtn").getAttribute("aria-expanded") === "true" && document.activeElement.id === "siteLinkName"), "«Список по ссылке» opens a form with a name and a link, the cursor in the name");
  const linkTry = async (name, url) => {
    await page.$eval("#siteLinkName", (e, x) => { e.value = x; }, name); await page.$eval("#siteLinkUrl", (e, x) => { e.value = x; }, url);
    await page.click("#siteLinkGo"); await sleep(250);
    return page.evaluate(() => { const e = document.getElementById("siteLinkErr"); return e ? e.textContent : null; });
  };
  let e1 = await linkTry("Ещё список", "http://lists.example.net/ru/abroad.txt"), e2 = await linkTry("Ещё список", "https://example.org/list.txt");
  ok(e1 === "нужна ссылка на файл списка, начинается с https://" && e2 === "этот список уже подключён" && (await bar()) === null, "a link that is not https and a link that is already connected are refused with a message: " + J([e1, e2]));
  let e3 = await linkTry("Ещё список", "https://lists.example.net/ru/abroad.txt");
  v = await snap(page); b = await bar();
  ok(e3 === null && v[0].name === "Ещё список" && v[0].linkIcon && /^lists\.example\.net\/ru/.test(v[0].meta || "") && b && b.n === 1 && (await posts("/api/sites")).length === n0 + 1,
    "a good link closes the form and stages the list as the first card with its source; nothing is sent yet: " + J({ err: e3, name: v[0].name, meta: v[0].meta, bar: b }));
  n0 = (await posts("/api/sites")).length;
  done = await apply(page);
  p = (await posts("/api/sites")).slice(n0); body = (p[0] || {}).body || { groups: [] };
  ok(done && p.length === 1 && J(body.groups[0]) === J({ id: null, name: "Ещё список", on: true, url: "https://lists.example.net/ru/abroad.txt" }), "«Применить» sends the new list as name + link: " + J(body.groups[0]));
  v = await snap(page);
  ok(v[0].name === "Ещё список" && v[0].count === "2" && v[0].chips.length === 0, "once downloaded the list shows its size (2), still not its entries: count=" + v[0].count);

  // ── search ─────────────────────────────────────────────────────────────────
  const groups = async () => (await snap(page)).filter(x => /^Группа /.test(x.label || ""));
  await fill(page, "#siteSearch", "tube");
  v = await groups();
  ok(v.map(x => x.name).join() === "YouTube" && has(chipOf(v[0], "youtube.com").cls, "hit"), "search by a part of an entry leaves the group that has it and highlights the chip: " + v.map(x => x.name).join());
  await fill(page, "#siteSearch", "Музы");
  v = await groups();
  ok(v.map(x => x.name).join() === "Музыка" && items(v[0]).join() === "spotify.com" && !v[0].chips.some(x => has(x.cls, "hit")), "search by a group name (any case) shows that group with its chips, none highlighted: " + v.map(x => x.name).join());
  await fill(page, "#siteSearch", "teleg");
  v = await groups();
  ok(v.map(x => x.name).join() === "Telegram" && has(chipOf(v[0], "telegram.org").cls, "hit") && !has(chipOf(v[0], "t.me").cls, "hit") && v[0].chips.some(x => has(x.cls, "auto")), "a text that is in the name and in an entry: the whole group, the matching chip highlighted: " + J(v.map(x => [x.name, x.chips.map(y => y.cls)])));
  await fill(page, "#siteSearch", "shop.e");
  v = await snap(page);
  ok(v.map(x => x.name).join() === "Список друга" && J(v[0].chips.map(x => x.t)) === J(["shop.example"]) && has(v[0].chips[0].cls, "hit"), "an entry of a list by link is shown only when it is searched for: " + J(v.map(x => [x.name, x.chips.map(y => y.t)])));
  await fill(page, "#siteSearch", "zzzz");
  ok((await page.$eval("#siteGrid", e => e.textContent)) === "Нет в списках", "a search that finds nothing says «Нет в списках»");
  const probe = () => page.evaluate(() => { const pc = document.querySelector("#siteGrid .probe"); if (!pc) return null;
    return { label: pc.getAttribute("aria-label"), first: pc === document.getElementById("siteGrid").firstElementChild, go: pc.querySelector("#probeGo").textContent, add: pc.querySelector("#probeAdd") ? pc.querySelector("#probeAdd").textContent : null,
      rows: [...pc.querySelectorAll(".probe-row")].map(r => ({ k: r.querySelector("span").textContent, v: r.querySelector("b").textContent, cls: r.querySelector("b").className })) }; });
  await fill(page, "#siteSearch", "www.youtube.com");
  let pr = await probe(); v = await groups();
  ok(pr && pr.label === "Проверка www.youtube.com" && pr.first && pr.rows.length === 1 && pr.rows[0].k === "Сейчас идёт" && pr.rows[0].v === "через заграницу · YouTube" && pr.add === null && pr.go === "Проверить",
    "a whole address gets the card «Проверка»: it already goes abroad through «YouTube», so there is nothing to add: " + J(pr));
  ok(v.map(x => x.name).join() === "YouTube" && has((chipOf(find(v, "YouTube"), "youtube.com") || {}).cls, "hit"), "and the entry that covers the address is highlighted in its group");
  const probes0 = (await apiLog()).filter(x => x.path === "/api/probe").length;
  await fill(page, "#siteSearch", "https://blocked.example.com/watch/page?v=1");
  pr = await probe();
  ok(pr && pr.label === "Проверка blocked.example.com" && pr.rows.length === 1 && pr.rows[0].v === "напрямую" && pr.go === "Проверить" && pr.add === "Добавить example.com",
    "a pasted link is read as its host; it goes directly, and «Добавить example.com» is offered: " + J(pr));
  ok((await apiLog()).filter(x => x.path === "/api/probe").length === probes0, "typing an address does not start the check by itself");
  await page.click("#probeGo");
  await page.waitForFunction(() => document.querySelectorAll("#siteGrid .probe .probe-row").length > 1, { timeout: 8000 }).catch(() => {});
  pr = await probe();
  ok(pr && J(pr.rows.slice(1).map(r => [r.k, r.v, r.cls])) === J([["Напрямую", "сайт отказывает (403)", "bad"], ["Через exit1-nl", "открывается · 250 мс", "good"], ["Через exit2-nl", "открывается · 310 мс", "good"]]) && pr.go === "Ещё раз",
    "«Проверить» adds a row for the direct way and for each exit: " + J(pr && pr.rows.slice(1)));
  let pl = (await apiLog()).filter(x => x.path === "/api/probe").slice(probes0);
  ok(pl.length === 1 && pl[0].query.host === "blocked.example.com" && pl[0].query.path === "/watch/page?v=1", "one check is asked of the server, with the host and the page of the pasted link: " + J(pl.map(x => x.query)));
  n0 = (await posts("/api/sites")).length;
  await page.click("#probeAdd"); await sleep(200);
  ok(J(await menu(page)) === J(["Музыка", "YouTube", "ИИ"]), "«Добавить example.com» offers the groups one can add to (not the automatic one, not the lists by link): " + J(await menu(page)));
  await pick(page, "Музыка");
  v = await snap(page); b = await bar(); pr = await probe();
  ok(has((chipOf(find(v, "Музыка"), "example.com") || {}).cls, "new") && b && b.n === 1 && (await posts("/api/sites")).length === n0, "the domain is staged in the chosen group (new chip, «Применить 1», nothing sent): " + J(items(find(v, "Музыка"))) + " " + J(b));
  ok(pr && pr.rows[0].v === "через заграницу · Музыка" && pr.add === null, "the card now says the address will go abroad through «Музыка»: " + J(pr && pr.rows[0]));
  await page.click("#siteCancel"); await sleep(200);
  await fill(page, "#siteSearch", ""); await page.evaluate(() => document.activeElement && document.activeElement.blur());
  ok((await snap(page)).length === 6 && (await bar()) === null, "an empty search shows every group again");

  // ── a stale save: the list was changed in another tab ──────────────────────
  await page.click(card("YouTube") + " .switch"); await sleep(200);
  const tab2 = await open(browser, "sites");
  await plusOf(tab2, "ИИ"); await enter(tab2, "siteInput", "tab-two.example");
  n0 = (await posts("/api/sites")).length;
  await apply(tab2);
  ok((await posts("/api/sites")).length === n0 + 1 && (await sitesNow()).groups.find(g => g.name === "ИИ").items.includes("tab-two.example"), "the second tab saved its change");
  await page.bringToFront(); await sleep(6500);
  v = await snap(page); b = await bar();
  ok(find(v, "YouTube").on === false && b && b.n === 1 && !chipOf(find(v, "ИИ"), "tab-two.example"), "the first tab keeps its staged change while the list changes elsewhere: " + J(b));
  n0 = (await posts("/api/sites")).length;
  await page.click("#siteApply");
  const refusedSave = await waitToast(page, "сайты изменились в другом окне");
  v = await snap(page); srv = await sitesNow();
  ok(refusedSave && (await posts("/api/sites")).length === n0 + 1 && srv.groups.find(g => g.name === "YouTube").on === true, "its «Применить» is refused with «сайты изменились в другом окне» and changes nothing on the server: " + J(await toasts(page)));
  ok(!!chipOf(find(v, "ИИ"), "tab-two.example") && !has((chipOf(find(v, "ИИ"), "tab-two.example") || {}).cls, "new") && find(v, "YouTube").on === true && (await bar()) === null, "and the page takes the new list: the other tab's entry is there, the own staged change and the bar are gone");
  // an idle tab re-reads the list by itself
  await tab2.bringToFront(); await tab2.focus("#siteInput").catch(() => {}); await tab2.keyboard.press("Escape"); await sleep(200);
  await page.bringToFront();
  await page.click(card("Музыка") + " .switch"); await sleep(150); await apply(page);
  await tab2.bringToFront();
  const synced = await tab2.waitForFunction(sel => { const s = document.querySelector(sel); return !!s && s.getAttribute("aria-checked") === "false"; }, { timeout: 12000 }, card("Музыка") + " .switch").then(() => true, () => false);
  ok(synced, "a tab with nothing staged takes a change made in another tab without a reload");
  await tab2.close();
  await page.bringToFront();

  // ── leaving with staged changes ────────────────────────────────────────────
  const stay = await open(browser, "sites", { dismiss: true });
  const asked = []; stay.on("dialog", d => asked.push(d.message()));
  await stay.click(card("YouTube") + " .switch"); await sleep(200);
  await stay.click('nav.tabs a[href="#/sites/blocked"]'); await sleep(400);
  st = await stay.evaluate(sel => ({ hash: location.hash, grid: !!document.getElementById("siteGrid"), on: document.querySelector(sel).getAttribute("aria-checked"), bar: !document.getElementById("siteActions").hidden }), card("YouTube") + " .switch");
  ok(asked.join() === "Изменения не применены. Уйти со страницы?" && st.hash === "#/sites" && st.grid && st.on === "false" && st.bar, "leaving with staged changes asks; «Отмена» keeps the page and what was staged: " + J(asked) + " " + J(st));
  asked.length = 0;
  await stay.click(`${card("YouTube")} button[aria-label="Действия: YouTube"]`); await sleep(200); await pick(stay, "Удалить");
  ok(asked.join() === "Удалить группу «YouTube» и её записи (1)?" && !!(await stay.$(card("YouTube"))), "«Отмена» on the question about deleting keeps the group: " + J(asked));
  await stay.click("#siteCancel"); await sleep(150);
  await stay.close();
  await page.bringToFront();
  n0 = (await posts("/api/sites")).length; dialogs.length = 0;
  await page.click(card("YouTube") + " .switch"); await sleep(200);
  await page.click('nav.tabs a[href="#/sites/blocked"]');
  await page.waitForSelector("#blGrid section", { timeout: 8000 }).catch(() => {});
  ok(dialogs.join() === "Изменения не применены. Уйти со страницы?" && (await page.evaluate(() => location.hash)) === "#/sites/blocked", "agreeing to leave opens «Блокируется»: " + J(dialogs));
  dialogs.length = 0;
  await page.click('nav.tabs a[href="#/sites"]');
  await page.waitForSelector(card("YouTube"), { timeout: 8000 }).catch(() => {});
  ok(find(await snap(page), "YouTube").on === true && (await bar()) === null && dialogs.length === 0 && (await posts("/api/sites")).length === n0, "coming back, the abandoned change is gone and was never sent");

  // ── «Блокируется» ──────────────────────────────────────────────────────────
  await page.click('nav.tabs a[href="#/sites/blocked"]');
  await page.waitForSelector("#blGrid section", { timeout: 8000 }).catch(() => {});
  const G = "blGrid", digits = s => (s || "").replace(/\s/g, "");
  const blPosts = async () => posts("/api/block");
  const blWait = async n => { for (let i = 0; i < 40 && (await blPosts()).length < n; i++) await sleep(100); await page.waitForFunction(() => !document.getElementById("page").classList.contains("busy"), { timeout: 5000 }).catch(() => {}); await sleep(150); return (await blPosts()).length; };
  v = await snap(page, G);
  ok(v.map(x => x.name).join() === "Реклама,Опасные сайты,AdAway,Свои", "the main list, «Опасные сайты», the other lists, then the admin's own groups: " + v.map(x => x.name).join());
  ok(v[0].on && digits(v[0].count) === "190777" && digits(v[0].day) === "5123засутки" && v[2].on && digits(v[2].count) === "6540" && digits(v[2].day) === "310засутки", "each list shows its switch, its size and what it blocked in a day: " + J([v[0].count, v[0].day, v[2].count, v[2].day]));
  ok(J(v[0].chips.map(x => [x.t, x.sub])) === J([["ads.example", "900"]]) && /Чаще всего за сутки/.test(v[0].text), "the main list shows what was blocked most often: " + J(v[0].chips));
  ok(v[1].on === true && digits(v[1].day) === "3засутки" && v[1].count === null && !v[1].plus, "«Опасные сайты» is on, with its count for the day and nothing to add to: " + J([v[1].on, v[1].day]));
  ok(v[3].on && v[3].count === "2" && items(v[3]).join() === "casino.example,ads.example" && v[3].plus, "the own group «Свои» shows its two addresses and a «+»: " + J(items(v[3])));
  let bn = (await blPosts()).length;
  await page.click(card("Реклама", G) + " .switch");
  ok((await blWait(bn + 1)) === bn + 1 && J((((await blPosts())[bn] || {}).body || {}).lists) === J({ 1: false, 2: true }), "the switch of a list is sent at once: POST /api/block with lists 1 → off, 2 → on: " + J(((await blPosts())[bn] || {}).body && (((await blPosts())[bn] || {}).body || {}).lists));
  bn = (await blPosts()).length;
  await page.click(card("Опасные сайты", G) + " .switch");
  let got = await blWait(bn + 1); v = await snap(page, G); c = find(v, "Опасные сайты");
  ok(got === bn + 1 && (((await blPosts())[bn] || {}).body || {}).safebrowsing === false && c.on === false && has(c.cls, "off") && c.day === null, "switching «Опасные сайты» off is sent at once and shown: " + J({ sent: ((await blPosts())[bn] || {}).body && (((await blPosts())[bn] || {}).body || {}).safebrowsing, on: c.on, day: c.day }));
  bn = (await blPosts()).length;
  await page.click(card("Опасные сайты", G) + " .switch"); await blWait(bn + 1);
  ok(find(await snap(page, G), "Опасные сайты").on === true && (((await blPosts())[bn] || {}).body || {}).safebrowsing === true, "and back on");

  await plusOf(page, "Свои", G);
  ok(await page.evaluate(sel => { const f = document.getElementById("blInput"); return !!f && document.querySelector(sel).contains(f) && document.activeElement === f; }, card("Свои", G)), "«+» of «Свои» opens a field inside the card");
  bn = (await blPosts()).length;
  const blRefused = {};
  for (const t of ["10.0.0.0/8", "not a domain!", "ads.example"]) blRefused[t] = await enter(page, "blInput", t);
  ok(blRefused["10.0.0.0/8"] === "сети здесь не указываются" && blRefused["not a domain!"] === "не похоже на домен" && blRefused["ads.example"] === "уже есть в группе «Свои»" && (await blPosts()).length === bn, "a network, garbage and an address that is already there are refused with a message and not sent: " + J(blRefused));
  await fill(page, "#blInput", "https://Bad-Site.example/login"); await page.keyboard.press("Enter");
  got = await blWait(bn + 1); body = ((await blPosts())[bn] || {}).body || { groups: [] }; v = await snap(page, G);
  ok(got === bn + 1 && J(body.groups) === J([{ id: "b00001", name: "Свои", on: true, kind: "block", items: ["casino.example", "ads.example", "bad-site.example"] }]) && !!chipOf(find(v, "Свои"), "bad-site.example") && find(v, "Свои").count === "3",
    "an added address is sent at once (one POST /api/block with the whole group) and appears as a chip: " + J(body.groups));
  bn = (await blPosts()).length;
  await page.click(chipSel("Свои", "casino.example", G) + " .chip-t"); await sleep(200); await pick(page, "Убрать");
  got = await blWait(bn + 1); body = ((await blPosts())[bn] || {}).body || { groups: [{}] }; v = await snap(page, G);
  ok(got === bn + 1 && J(body.groups[0].items) === J(["ads.example", "bad-site.example"]) && !chipOf(find(v, "Свои"), "casino.example"), "removing a chip is sent at once and the chip is gone: " + J(body.groups[0].items));
  bn = (await blPosts()).length;
  await page.click(card("Свои", G) + " .switch");
  got = await blWait(bn + 1); body = ((await blPosts())[bn] || {}).body || { groups: [{}] }; c = find(await snap(page, G), "Свои");
  ok(got === bn + 1 && body.groups[0].on === false && c.on === false && has(c.cls, "off"), "the switch of an own group is sent at once and shown: sent on=" + body.groups[0].on + " shown on=" + c.on);
  bn = (await blPosts()).length;
  await page.click(card("Свои", G) + " .switch"); await blWait(bn + 1);
  await page.evaluate(() => [...document.querySelectorAll(".toolbar .tools button")].find(x => x.textContent === "Новая группа").click()); await sleep(200);
  bn = (await blPosts()).length;
  await page.keyboard.type("Соцсети"); await page.keyboard.press("Enter");
  got = await blWait(bn + 1); body = ((await blPosts())[bn] || {}).body || { groups: [{}] }; v = await snap(page, G);
  ok(got === bn + 1 && J(body.groups[0]) === J({ id: null, name: "Соцсети", on: true, kind: "block", items: [] }) && body.groups.length === 2 && !find(v, "Соцсети").missing && find(v, "Соцсети").field, "«Новая группа» is created at once and opens with its field ready: " + J(body.groups[0]));
  bn = (await blPosts()).length;
  await fill(page, "#blInput", "vk*.example.net"); await page.keyboard.press("Enter");
  got = await blWait(bn + 1); c = find(await snap(page, G), "Соцсети");
  ok(got === bn + 1 && chipOf(c, "vk*.example.net") && chipOf(c, "vk*.example.net").star, "a pattern is accepted in an own group and shown with its star: " + J(c.chips));
  await page.keyboard.press("Escape"); await sleep(150);
  await cardMenu(page, "Соцсети", G); await pick(page, "Переименовать");
  bn = (await blPosts()).length;
  await page.keyboard.type("Соцсети и игры"); await page.keyboard.press("Enter");
  got = await blWait(bn + 1); v = await snap(page, G);
  ok(got === bn + 1 && !find(v, "Соцсети и игры").missing, "renaming an own group is sent at once: " + v.map(x => x.name).join());
  bn = (await blPosts()).length; dialogs.length = 0;
  await cardMenu(page, "Соцсети и игры", G); await pick(page, "Удалить");
  got = await blWait(bn + 1); body = ((await blPosts())[bn] || {}).body || { groups: [] }; v = await snap(page, G);
  ok(dialogs.join() === "Удалить группу «Соцсети и игры» и её записи (1)?" && got === bn + 1 && body.groups.map(g => g.name).join() === "Свои" && find(v, "Соцсети и игры").missing, "deleting an own group asks, is sent at once, and the card is gone: " + J(dialogs));

  // search on «Блокируется»
  await fill(page, "#blSearch", "ads.e");
  v = await snap(page, G);
  ok(v.map(x => x.name).join() === "Свои" && has(chipOf(v[0], "ads.example").cls, "hit") && !has(chipOf(v[0], "bad-site.example").cls, "hit"), "search by a part of an entry finds the own group and highlights the chip: " + v.map(x => x.name).join());
  await fill(page, "#blSearch", "adaw");
  ok((await snap(page, G)).map(x => x.name).join() === "AdAway", "search by name finds a list: " + (await snap(page, G)).map(x => x.name).join());
  await sleep(600);
  let ch0 = (await apiLog()).filter(x => x.path === "/api/check").length;
  ok(ch0 === 0, "a part of a name is not asked of the blocker: " + ch0 + " checks so far");
  await fill(page, "#blSearch", "https://www.ads.example/banner.js");
  await page.waitForFunction(() => document.querySelector("#blGrid .chip.hit"), { timeout: 6000 }).catch(() => {});
  for (let i = 0; i < 30 && (await apiLog()).filter(x => x.path === "/api/check").length === ch0; i++) await sleep(100);
  await sleep(300);
  let cl = (await apiLog()).filter(x => x.path === "/api/check").slice(ch0); v = await snap(page, G);
  ok(cl.length === 1 && cl[0].query.host === "www.ads.example", "a whole address (here a pasted link) is asked of the blocker once: " + J(cl.map(x => x.query)));
  ok(v.map(x => x.name).join() === "Свои" && has((chipOf(v[0], "ads.example") || {}).cls, "hit"), "the group that blocks it is shown with the rule highlighted: " + J(v.map(x => [x.name, x.chips.filter(y => has(y.cls, "hit")).map(y => y.t)])));
  ch0 = (await apiLog()).filter(x => x.path === "/api/check").length;
  await fill(page, "#blSearch", "clean.example.org");
  const free = await page.waitForFunction(() => document.getElementById("blGrid").textContent === "Не блокируется", { timeout: 6000 }).then(() => true, () => false);
  cl = (await apiLog()).filter(x => x.path === "/api/check").slice(ch0);
  ok(free && cl.length === 1 && cl[0].query.host === "clean.example.org", "an address nothing blocks: the blocker is asked and the page says «Не блокируется»: " + J(await page.$eval("#blGrid", e => e.textContent.slice(0, 60))));
  await fill(page, "#blSearch", "zzzz");
  ok((await page.$eval("#blGrid", e => e.textContent)) === "Ничего не найдено", "a part of a name that is nowhere: «Ничего не найдено»");
  await fill(page, "#blSearch", "");
  ok((await snap(page, G)).map(x => x.name).join() === "Реклама,Опасные сайты,AdAway,Свои", "an empty search shows everything again");
  ok((await posts("/api/block/lists")).length === 0 && (await posts("/api/sites/refresh")).length === 0, "nothing in this file touched the lists by link behind the page's back");
  await finish(browser, [page]);
});
