// «Сеть»: changes made on the canvas are staged, drawn at once, and sent only by «Применить», one kind after another.
const { sleep, ok, ctl, changes, connect, open, toasts, finish, run } = require("./lib");
run(async () => {
  const browser = await connect();
  const page = await open(browser, "network");
  const J = v => JSON.stringify(v);
  await page.waitForFunction(() => document.querySelectorAll("#net .node.rule").length > 0, { timeout: 8000 }).catch(() => {});
  const bar = () => page.evaluate(() => { const e = document.querySelector(".net-pending span"); return e ? e.textContent : null; });
  const staged = () => page.evaluate(() => [...document.querySelectorAll("#net .node.staged, #net .share.staged")].map(e => e.dataset.k || e.closest("[data-k]").dataset.k));
  // a block found by the name it shows
  const pick = async name => {
    const k = await page.evaluate(name => { const b = [...document.querySelectorAll("#net [data-k] b")].find(e => e.textContent === name); return b ? b.closest("[data-k]").dataset.k : null; }, name);
    const node = k && (await page.$('#net [data-k="' + k + '"]'));
    if (node && (await node.evaluate(e => e.getAttribute("aria-current"))) !== "true") await node.click(); // a second click would close it
    await sleep(500);
    return k;
  };
  const press = async (groupLabel, text) => {
    const done = await page.evaluate((groupLabel, text) => {
      const group = document.querySelector('#netSide [aria-label="' + groupLabel + '"]');
      const button = group && [...group.querySelectorAll("button")].find(b => b.textContent === text);
      if (button) button.click();
      return !!button;
    }, groupLabel, text);
    await sleep(300);
    return done;
  };
  const edgeInto = from => page.evaluate(from => !!document.querySelector('#net .react-flow__edge[data-id="e:out:' + from + '"]'), from);

  ok((await bar()) === null && (await page.$$("#net .node.rule")).length === 7 && (await page.$$("#net .node.target")).length === 3,
    "the canvas has the rules (five groups of sites, one blocked group, «Остальное») and the three targets; nothing is staged");

  // ── a group: its protection and its exit ──
  const home = await pick("Дом");
  ok(await press("Защита группы", "Pro"), "the inspector of a group offers its protection");
  await page.select("#netSide select", "1"); await sleep(300);
  ok((await bar()) === "1 изменение" && (await staged()).includes(home) && /→ exit1-nl/.test(await page.$eval('#net [data-k="' + home + '"]', e => e.textContent)),
    "protection and exit of one group are one change, drawn on its block: " + J([await bar(), await staged()]));

  // ── a group of sites switched on ──
  const work = await pick("Работа");
  ok(!(await edgeInto(work)) && await press("Куда идёт правило", "За границу") && await edgeInto(work), "«Работа» switched on: its link to «За границу» appears at once");
  // ── a blocked group switched off ──
  const own = await pick("Свои");
  ok(await press("Куда идёт правило", "Выключено") && !(await edgeInto(own)), "«Свои» switched off: its link to «Блок» is gone");
  // ── the shares ──
  await pick("За границу");
  const share = await page.$('#netSide input[aria-label^="Доля exit1-nl"]');
  await share.click({ clickCount: 3 }); await share.type("50"); await sleep(300);
  ok(J(await page.$$eval("#net .share", e => e.map(x => x.textContent))) === J(["50%", "50%"]), "new shares are drawn on the exits");
  // ── the mode ──
  await pick("Остальное");
  ok(await press("Режим сети", "Зарубежное — за границу"), "«Остальное» offers the network's mode");
  ok((await bar()) === "5 изменений" && (await changes()).length === 0, "five changes staged, nothing sent: " + J([await bar(), (await changes()).map(x => x.path)]));

  // ── «Применить»: in order, each through its own request ──
  await page.click("#netApply");
  await page.waitForFunction(() => !document.querySelector(".net-pending"), { timeout: 15000 }).catch(() => {});
  const sent = await changes();
  ok(J(sent.map(x => x.path)) === J(["/api/ugroups", "/api/rules/route", "/api/mode", "/api/exits/weights"]), "«Применить» sends groups, the rules (all in one request), mode, shares — in that order: " + J(sent.map(x => x.path)));
  const body = path => (sent.find(x => x.path === path) || {}).body || {};
  const g = (body("/api/ugroups").groups || []).find(x => x.id === "g00001") || {};
  ok(g.filter === "pro" && g.exit === 1, "the group was sent with Pro and exit1-nl: " + J(g));
  const routes = body("/api/rules/route").changes;
  ok(J(routes) === J([{ rule: work.slice(2), route: "exit" }, { rule: "b00001", route: "off" }]), "both rules were sent together, each with where it now leads: " + J(routes));
  ok(body("/api/mode").mode === "geo" && J(body("/api/exits/weights").weights) === J({ 1: 50, 3: 50 }), "the mode and the shares: " + J([body("/api/mode"), body("/api/exits/weights")]));
  ok((await bar()) === null && (await staged()).length === 0 && (await toasts(page)).some(t => /применены/.test(t)), "after it: nothing staged, «Изменения применены»");

  // ── a refusal stops the rest, and what was not sent stays ──
  await sleep(1500);
  const before = (await changes()).length;
  await pick("Офис");
  await press("Защита группы", "Ultimate");
  await pick("Работа");
  await press("Куда идёт правило", "Выключено");
  await ctl({ fail: "/api/ugroups" });
  await page.click("#netApply"); await sleep(2500);
  const after = (await changes()).slice(before).map(x => x.path);
  ok(J(after) === J(["/api/ugroups"]) && (await bar()) === "2 изменения" && (await toasts(page)).some(t => /Применено не всё/.test(t)),
    "the server refused the groups: the sites were not sent, both changes stay staged, the admin is told: " + J([after, await bar()]));

  // ── «Отменить» ──
  const sentBefore = (await changes()).length;
  await page.evaluate(() => [...document.querySelectorAll(".net-pending button")].find(b => b.textContent === "Отменить").click()); await sleep(400);
  ok((await bar()) === null && (await staged()).length === 0 && (await changes()).length === sentBefore, "«Отменить» drops what was staged and sends nothing");
  // ── a rule moved between abroad and blocked ──
  ok(!(await (async () => { await pick("Работа"); return press("Куда идёт правило", "Блок"); })()), "«Работа» has a network: it cannot be blocked, «Блок» is not offered");
  const ai = await pick("ИИ");
  ok(await press("Куда идёт правило", "Блок") && await page.evaluate(k => !!document.querySelector('#net .react-flow__edge[data-id="e:out:' + k + '"][class*="react-flow__edge"]'), ai), "«ИИ» staged as blocked: its link now goes to «Блок»");
  const movedBefore = (await changes()).length;
  await page.click("#netApply");
  await page.waitForFunction(() => !document.querySelector(".net-pending"), { timeout: 15000 }).catch(() => {});
  await sleep(1500);
  const moved = (await changes()).slice(movedBefore);
  const aiNow = await page.evaluate(() => { const b = [...document.querySelectorAll("#net [data-k] b")].find(e => e.textContent === "ИИ"); const n = b && b.closest("[data-k]"); return n ? [n.dataset.k, n.classList.contains("to-block")] : null; });
  ok(J(moved.map(x => [x.path, x.body.changes[0].route])) === J([["/api/rules/route", "block"]]) && J(aiNow) === J([ai, true]),
    "«Применить» sent «ИИ» to the block: one request, the same rule (same id) drawn as blocked now: " + J([moved.map(x => x.path), aiNow]));
  // ── a rule sent directly ──
  const tg = await pick("Telegram");
  ok(!(await page.evaluate(() => [...document.querySelectorAll('#netSide [aria-label="Куда идёт правило"] button')].some(b => b.textContent === "Напрямую"))),
    "Telegram's networks update themselves: «Напрямую» is not offered");
  const yt = await pick("YouTube");
  await press("Куда идёт правило", "Напрямую");
  const directBefore = (await changes()).length;
  await page.click("#netApply");
  await page.waitForFunction(() => !document.querySelector(".net-pending"), { timeout: 15000 }).catch(() => {});
  await sleep(1500);
  const sentDirect = (await changes()).slice(directBefore).map(x => x.body && x.body.changes);
  const directEdge = await page.evaluate(k => { const n = document.querySelector('#net [data-k="' + k + '"]'); return !!n && n.classList.contains("to-direct") && !!document.querySelector('#net .react-flow__edge[data-id="e:out:' + k + '"]'); }, yt);
  ok(J(sentDirect) === J([[{ rule: yt.slice(2), route: "direct" }]]) && directEdge, "«YouTube» sent directly: one request, its link now leads to «Напрямую»: " + J(sentDirect));
  ok(!!tg, "the canvas has the Telegram rule");
  const sitesTab = await open(browser, "sites");
  const tagged = await sitesTab.evaluate(() => [...document.querySelectorAll("section.group")].filter(e => e.querySelector(".badge.go-direct")).map(e => e.querySelector(".group-name").textContent));
  await sitesTab.close();
  ok(J(tagged) === J(["YouTube"]), "«Сайты» mark the group sent directly: " + J(tagged));
  // ── the order: «Выше» stages it, «Применить» sends it with the version the canvas showed ──
  const own2 = await pick("Свои");
  const orderBefore = await page.evaluate(async () => (await (await fetch("/api/rules")).json()));
  ok(await page.evaluate(() => { const b = [...document.querySelectorAll("#netSide button")].find(x => x.textContent === "Выше"); if (b) b.click(); return !!b; }) && (await bar()) === "1 изменение",
    "«Выше» in the inspector of a rule stages one change: the order");
  const orderSentBefore = (await changes()).length;
  await page.click("#netApply");
  await page.waitForFunction(() => !document.querySelector(".net-pending"), { timeout: 15000 }).catch(() => {});
  await sleep(1000);
  const orderSent = (await changes()).slice(orderSentBefore).map(x => x.body);
  const ids = orderBefore.rules.map(r => r.id);
  const at = ids.indexOf(own2.slice(2));
  const expected = ids.slice();
  [expected[at - 1], expected[at]] = [expected[at], expected[at - 1]];
  ok(orderSent.length === 1 && J(orderSent[0].order) === J(expected) && orderSent[0].base === orderBefore.v && J(orderSent[0].changes) === "[]",
    "«Применить» sends the new order (one swap) with the version it was based on: " + J(orderSent));
  // ── a site in two rules: the lower one is marked covered ──
  await page.evaluate(async () => {
    const sites = await (await fetch("/api/sites")).json();
    const groups = [...sites.groups, { name: "Дубль", on: true, items: ["youtube.com"] }];
    await fetch("/api/sites", { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" }, body: JSON.stringify({ base: sites.v, groups }) });
  });
  await page.reload({ waitUntil: "networkidle0" }); await sleep(1200);
  const dup = await pick("Дубль");
  const covered = await page.evaluate(() => [...document.querySelectorAll("#netSide .chip.shadowed")].map(e => e.textContent + " | " + e.title));
  ok(J(covered) === J(["youtube.com | Перекрыто правилом выше: «YouTube» — срабатывает оно"]) && /перекрыто/.test(await page.$eval('#net [data-k="' + dup + '"]', e => e.textContent)),
    "a site already in a rule above: marked covered in the inspector and on the block, with the rule that decides: " + J(covered));
  // ── a rule for some groups of people: blocking only, for now ──
  const ownRule = await pick("Свои");
  const offScope = await page.evaluate(() => [...document.querySelectorAll('#netSide [aria-label="Для кого"] button')].filter(b => b.disabled).length);
  ok(offScope === 3, "«Свои» is off now: its groups cannot be chosen (a rule for some groups only blocks, for now)");
  await press("Куда идёт правило", "Блок");
  const scopeButtons = () => page.evaluate(() => [...document.querySelectorAll('#netSide [aria-label="Для кого"] button')].map(b => b.textContent + (b.disabled ? ":off" : "") + (b.getAttribute("aria-pressed") === "true" ? ":on" : "")));
  ok(J(await scopeButtons()) === J(["Все:on", "Дом", "Офис", "Дача"]), "«Для кого»: все, or the groups of people: " + J(await scopeButtons()));
  await page.evaluate(() => [...document.querySelectorAll('#netSide [aria-label="Для кого"] button')].find(b => b.textContent === "Дача").click()); await sleep(300);
  const routeButtons = await page.evaluate(() => [...document.querySelectorAll('#netSide [aria-label="Куда идёт правило"] button')].map(b => b.textContent));
  ok((await bar()) === "1 изменение" && /только: Дача/.test(await page.$eval('#net [data-k="' + ownRule + '"]', e => e.textContent)) && !routeButtons.includes("За границу"),
    "«Дача» chosen: one change, the block says «только: Дача», and «За границу» is not offered for a group: " + J(routeButtons));
  const scopeBefore = (await changes()).length;
  await page.click("#netApply");
  await page.waitForFunction(() => !document.querySelector(".net-pending"), { timeout: 15000 }).catch(() => {});
  await sleep(1000);
  const scopeSent = (await changes()).slice(scopeBefore).map(x => x.body.changes);
  ok(J(scopeSent) === J([[{ rule: ownRule.slice(2), route: "block", groups: ["g00003"] }]]), "«Применить» sends the rule's route and groups as one change: " + J(scopeSent));
  // ── a refusal of a rule ──
  await pick("YouTube");
  await press("Куда идёт правило", "Выключено");
  await ctl({ fail: "/api/rules/route" });
  await page.click("#netApply"); await sleep(2500);
  ok((await toasts(page)).some(t => /Применено не всё/.test(t)) && (await bar()) === "1 изменение", "the server refused the rule: the canvas says so, the change stays staged: " + J(await toasts(page)));
  // ── «Блокируется» left open in another tab: its save is refused, it does not undo what changed since ──
  const stale = await open(browser, "sites/blocked");
  await sleep(600);
  await page.evaluate(async () => {
    const block = await (await fetch("/api/block")).json();
    const groups = block.groups.map(group => ({ ...group, name: group.name + " (переименовано)" }));
    await fetch("/api/block", { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" }, body: JSON.stringify({ groups, base: block.v }) });
  });
  const blockBefore = await page.evaluate(async () => (await (await fetch("/api/block")).json()).groups.map(g => g.name + ":" + g.on));
  await stale.evaluate(() => { const s = document.querySelector('.group-head button[role="switch"]'); if (s) s.click(); });
  await sleep(1500);
  const blockAfter = await page.evaluate(async () => (await (await fetch("/api/block")).json()).groups.map(g => g.name + ":" + g.on));
  const said = await toasts(stale);
  ok(J(blockAfter) === J(blockBefore) && said.some(t => /в другом окне/.test(t)), "a stale «Блокируется» tab is refused and changes nothing: " + J([blockBefore, blockAfter, said]));
  await stale.close();
  await finish(browser, [page]);
});
