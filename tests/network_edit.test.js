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
  const aiNow = await page.evaluate(() => { const b = [...document.querySelectorAll("#net [data-k] b")].find(e => e.textContent === "ИИ"); return b ? b.closest("[data-k]").dataset.k : null; });
  ok(J(moved.map(x => [x.path, x.body.changes[0].route])) === J([["/api/rules/route", "block"]]) && /^b:/.test(aiNow || ""), "«Применить» moved «ИИ» to the blocked groups: one request, it is drawn as a blocked rule now: " + J([moved.map(x => x.path), aiNow]));
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
  // ── a refusal of a rule ──
  await pick("YouTube");
  await press("Куда идёт правило", "Выключено");
  await ctl({ fail: "/api/rules/route" });
  await page.click("#netApply"); await sleep(2500);
  ok((await toasts(page)).some(t => /Применено не всё/.test(t)) && (await bar()) === "1 изменение", "the server refused the rule: the canvas says so, the change stays staged: " + J(await toasts(page)));
  await finish(browser, [page]);
});
