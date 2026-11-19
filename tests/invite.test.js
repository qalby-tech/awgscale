// Invitations: the admin makes a link in the panel; a stranger's browser opens
// the public page behind it, names a device and gets a user in that group.
const { BASE, sleep, ok, changes, connect, open, pick, toasts, finish, run } = require("./lib");
run(async () => {
  const browser = await connect();
  const page = await open(browser, "users");
  const groupMenu = async name => { await page.click('#userBlocks button[aria-label="Действия: ' + name + '"]'); await sleep(250); };
  await groupMenu("Дом");
  ok(await pick(page, "Пригласить"), "the group menu offers «Пригласить»");
  await page.waitForSelector("#invMake");
  let st = await page.evaluate(() => ({ title: document.querySelector("#codePanel .panel-title").textContent, link: !!document.getElementById("invLink"), rows: document.querySelectorAll(".inv-row").length }));
  ok(/Приглашение/.test(st.title) && /Дом/.test(st.title) && !st.link && st.rows === 0, "the panel opens for «Дом», with no link yet and no invitations: " + JSON.stringify(st));
  ok((await changes()).length === 0, "opening the panel sends nothing that changes anything");
  await page.select("#invUses", "2"); await page.select("#invDays", "7");
  await page.click("#invMake"); await page.waitForSelector("#invLink");
  const link = await page.$eval("#invLink", e => e.value);
  let ch = await changes();
  ok(ch.length === 1 && ch[0].path === "/api/invites" && ch[0].body.group === "g00001" && ch[0].body.uses === 2 && ch[0].body.days === 7, "one request: the group, 2 devices, 7 days: " + JSON.stringify(ch[0] && ch[0].body));
  ok(new RegExp("^" + BASE.replace(/[.]/g, "\\.") + "/i/[A-Za-z0-9_-]{22}$").test(link), "the link is shown: " + link.replace(/.{16}$/, "…"));
  const tg = await page.$eval("#invTg", e => e.getAttribute("href"));
  ok(tg.startsWith("https://t.me/share/url?url=" + encodeURIComponent(link) + "&text="), "«Отправить в Telegram» opens Telegram's share dialog with this very link");
  ok(await page.$eval(".inv-row span", e => /осталось 2 из 2 · до \d+\.\d\d/.test(e.textContent)), "…and listed as «осталось 2 из 2 · до dd.mm»: " + await page.$eval(".inv-row span", e => e.textContent));

  // ── the invited person's browser (no session, a different page) ───────────
  const guest = await browser.newPage(); await guest.setViewport({ width: 390, height: 844 });
  const gerr = []; guest.on("pageerror", e => gerr.push(String(e).slice(0, 200)));
  let asked = 0; guest.on("dialog", d => { asked++; d.accept(); });      // "leave the page?" while a key is on it
  await guest.goto(link, { waitUntil: "networkidle0" });
  await guest.waitForFunction(() => !document.getElementById("stepName").hidden);
  st = await guest.evaluate(() => ({ sub: document.getElementById("sub").textContent, foot: document.getElementById("foot").textContent, wide: document.documentElement.scrollWidth <= window.innerWidth, key: !document.getElementById("stepKey").hidden }));
  ok(/Дом/.test(st.sub) && /Осталось устройств: 2/.test(st.foot) && st.wide && !st.key, "the public page names the group and what is left, fits a phone, shows no key yet: " + JSON.stringify(st));
  await guest.type("#name", "!!! ???"); await guest.click("#go"); await sleep(300);
  ok((await guest.$eval("#err", e => e.textContent)).length > 0 && (await changes()).length === 1, "a bad device name is refused on the page and nothing is sent");
  await guest.$eval("#name", e => { e.value = ""; }); await guest.type("#name", "anna_phone"); await guest.click("#go");
  await guest.waitForFunction(() => document.getElementById("err").textContent.length > 0 && !document.getElementById("go").disabled);
  ok(/занято/.test(await guest.$eval("#err", e => e.textContent)) && await guest.$eval("#stepKey", e => e.hidden), "a name that is taken is refused by the server with a clear reason, and no key is shown");
  await guest.$eval("#name", e => { e.value = ""; }); await guest.type("#name", "Планшет бабушки"); await guest.click("#go");
  await guest.waitForFunction(() => !document.getElementById("stepKey").hidden);
  st = await guest.evaluate(() => ({ title: document.getElementById("title").textContent, href: document.getElementById("open").getAttribute("href"), qr: !document.getElementById("qr").hidden, foot: document.getElementById("foot").textContent, form: document.getElementById("stepName").hidden, uri: document.getElementById("uri").value }));
  ok(/planshet_babushki/.test(st.title) && /^vpn:\/\//.test(st.href) && /^vpn:\/\//.test(st.uri) && st.qr && st.form && /ещё устройств: 1/.test(st.foot), "a name typed in Russian with a space becomes planshet_babushki and gives the key: the vpn:// button, the string in sight, the QR; one device is left: " + JSON.stringify({ t: st.title, f: st.foot }));
  const sent = (await changes()).filter(c => c.path.startsWith("/i/")).map(c => c.body.name);
  ok(sent.join() === "anna_phone,planshet_babushki", "exactly two attempts reached the server: " + sent.join());
  await guest.reload({ waitUntil: "networkidle0" }); await sleep(400);
  st = await guest.evaluate(() => ({ title: document.getElementById("title").textContent, key: !document.getElementById("stepKey").hidden, uri: document.getElementById("uri").value }));
  ok(/planshet_babushki/.test(st.title) && st.key && /^vpn:\/\//.test(st.uri) && asked >= 1 && (await changes()).filter(c => c.path.startsWith("/i/")).length === 2, "a reload asks before leaving and then shows the same key again without using the link a second time");
  await guest.evaluate(() => { document.getElementById("copy").click(); }); await sleep(1300);
  ok(await guest.evaluate(() => /Скопировано/.test(document.getElementById("copy").textContent) || document.getElementById("err2").textContent.length > 0), "«Скопировать строку» either copies or says that it could not, never silently nothing");
  ok(gerr.length === 0, "no script errors on the public page: " + JSON.stringify(gerr));

  // ── back in the panel ─────────────────────────────────────────────────────
  await page.bringToFront(); await sleep(6500);
  ok(await page.evaluate(() => { const s = document.querySelector('section[aria-label="Группа Дом"]'); return /^5/.test(s.querySelector(".group-count").textContent); }), "the new user is counted in «Дом» after the next poll");
  ok(await page.$eval(".inv-row span", e => /^эта ссылка/.test(e.textContent)), "the row of the link just made is marked");
  await groupMenu("Дом"); await pick(page, "Пригласить"); await page.waitForSelector(".inv-row");
  ok(await page.$eval(".inv-row span", e => /осталось 1 из 2/.test(e.textContent) && /planshet_babushki/.test(e.textContent)) && !(await page.$("#invLink")), "reopened, the invitation shows 1 of 2 left and who used it; the link itself is not shown again");
  await page.click(".inv-row .btn"); await sleep(600);
  ok((await page.$$(".inv-row")).length === 0 && (await toasts(page)).some(t => /отозвано/.test(t)), "«Отозвать» removes it");
  const other = await browser.newPage(); other.on("dialog", d => d.accept());      // somebody else with the same link (this tab has no key kept)
  await other.goto(link, { waitUntil: "networkidle0" }); await other.evaluate(() => sessionStorage.clear()); await other.reload({ waitUntil: "networkidle0" }); await sleep(400);
  ok(/недействительна/.test(await other.$eval("#sub", e => e.textContent)) && await other.$eval("#stepName", e => e.hidden), "for anybody else the revoked link now says it is not valid and offers nothing");
  await other.close();
  await guest.evaluate(() => sessionStorage.clear());
  await guest.goto(BASE + "/i/" + "x".repeat(22), { waitUntil: "networkidle0" }); await sleep(300);
  ok(/недействительна/.test(await guest.$eval("#sub", e => e.textContent)), "a made-up link says the same");
  await finish(browser, [page, guest]);
});
