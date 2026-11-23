#!/usr/bin/env python3
"""The agent's store (awgscale.agent.store) on one real, throwaway Postgres:
what it writes it reads back the same, it refuses what it must in the words the
panel shows, a change made by one writer cannot overwrite a change made by
another (the same lock files), and the tables and the rows a document becomes
are the ones the deployed database holds (tests/golden/agent_store.json).

    PG_DSN="host=127.0.0.1 port=35441 dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=... uv run python tests/agent_store.py

Nothing live is touched."""
import asyncio
import hashlib
import json
import os
import random
import sys
import tempfile
import threading
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="agent-store-")
import psycopg  # noqa: E402

from awgscale.agent.errors import Refused, StoreDown, StoreRefused  # noqa: E402
from awgscale.agent.locks import file_lock  # noqa: E402
from awgscale.agent.store import documents  # noqa: E402
from awgscale.agent.store.documents import Kind  # noqa: E402
from awgscale.agent.store.store import Store, same  # noqa: E402

DSN = os.environ["PG_DSN"]
PASSWORD_FILE = Path(TMP) / "password"
PASSWORD_FILE.write_text(os.environ["PG_PASSWORD"] + "\n")
USERS_LOCK = Path(TMP) / "users.lock"
NEW = Store(DSN, PASSWORD_FILE)
GOLDEN = json.loads((Path(HERE) / "golden" / "agent_store.json").read_text())
BUSY = "сервер занят другой операцией — попробуйте ещё раз"
LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
ORDERED = [kind for kind in Kind if kind not in documents.MAPPED_KINDS]


def run(work):
    return LOOP.run_until_complete(work)


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:500])


def sql(q, *a):
    with psycopg.connect(DSN, password=os.environ["PG_PASSWORD"], autocommit=True) as c:
        cur = c.execute(q, a)
        return cur.fetchall() if cur.description else None


def wipe():
    sql("DROP SCHEMA public CASCADE")
    sql("CREATE SCHEMA public")


def refusal(fn, *a):
    """→ (which kind of no, its words)."""
    try:
        fn(*a)
    except StoreDown as e:
        return "down", str(e)
    except ValueError as e:
        return "refused", str(e)
    return None, None


def new_get(kind):
    return run(NEW.get(kind))


def new_put(kind, value):
    return run(NEW.put(kind, value))


ODD = {
    Kind.RULES: [{"id": "r00001", "name": "YouTube", "on": True, "route": "exit", "items": ["youtube.com"]},
                 {"id": "r00002", "name": "", "on": False, "route": 5, "items": "not-a-list", "url": "https://example.org/l.txt"}],
    Kind.SITES: [{"id": "a00001", "name": "YouTube", "on": True, "items": ["youtube.com", "ytimg.com"]},
                 {"id": "a00002", "name": "Сеть", "on": False, "items": [], "auto": "telegram"},
                 {"id": "a00003", "name": 7, "on": "yes", "items": ["x.example", 5], "url": "https://example.org/l.txt", "updated": 1700000000, "error": ""},
                 {"id": "a00004", "on": None, "nested": {"k": [1, {"z": None}]}}],
    Kind.BLOCK: [{"id": "b00001", "name": "Свои", "on": True, "items": ["casino.example"]}, {"id": "b00002", "name": "", "on": False, "items": "not-a-list"}],
    Kind.SPACES: [{"id": "s00001", "name": "Игры", "on": True, "members": ["ivan", "max"]}, {"id": "s00002", "name": None, "members": []}],
    Kind.USER_GROUPS: [{"id": "g00001", "name": "Дом", "space": False, "exit": None, "filter": "std", "members": ["anna", "tv"]},
                       {"id": "g00002", "name": "Офис", "space": True, "exit": 3, "filter": "kids", "members": []},
                       {"id": "g00003", "name": "Странная", "space": 1, "exit": "3", "filter": None, "members": ["a", "a"], "more": True}],
    Kind.INVITES: [{"id": "i00001", "hash": "ab" * 32, "group": "g00001", "left": 2, "total": 3, "expires_at": 1999999999, "created": 1700000000, "used": ["phone"]},
                   {"id": "i00002", "hash": "cd" * 32, "group": None, "left": 1, "total": 1, "expires_at": 1999999999.5, "created": True, "used": []}],
}
BAD = [None, {}, "text", [1], [{}], [{"id": ""}], [{"id": 5}], [{"id": "x"}, {"id": "x"}], [{"name": "no id"}]]


def t_empty():
    print("\n— пустая база")
    wipe()
    no = refusal(new_get, Kind.SITES)
    ok(no == ("down", "база пуста: настройки в неё не перенесены (store-init)"), "база без настроек: «недоступно», и сказано почему", no)
    ok(refusal(new_put, Kind.SITES, [])[0] == "down" and refusal(run, NEW.ping())[0] == "down", "…и ничего не пишет в неё")
    rules_tables = [statement for statement in documents.schema() if " rule" in statement]
    ok([statement for statement in documents.schema() if statement not in rules_tables] == GOLDEN["schema"], "таблицы описаны так же, как в базе на сервере", documents.schema())
    ok(len(rules_tables) == 2 and "CREATE TABLE IF NOT EXISTS rules (id text PRIMARY KEY, pos integer NOT NULL, name text, enabled boolean, route text, listed boolean NOT NULL, extra jsonb NOT NULL)" in rules_tables,
       "и две новые: правила и их записи", rules_tables)


def ready():
    wipe()

    async def make():
        async with NEW.transaction() as tx:
            await tx.create_schema()
    run(make())
    sql("INSERT INTO meta (key, value) VALUES ('ready', '1')")


def t_roundtrip():
    print("\n— что записано, то и читается")
    ready()
    for kind in ORDERED:
        doc = ODD[kind]
        new_put(kind, doc)
        by_new = new_get(kind)
        ok(by_new == doc and same(by_new, doc), f"{kind.value}: записанное читается то же, слово в слово", by_new)
        turned = list(reversed(doc))
        new_put(kind, turned)
        ok(new_get(kind) == turned and run(Store(DSN, PASSWORD_FILE).get(kind)) == turned, f"{kind.value}: переписанное читается то же и другим подключением, порядок сохранён")
        new_put(kind, [])
        ok(new_get(kind) == [] and sql(f"SELECT count(*) FROM {documents.TABLES[kind].list_table}")[0][0] == 0, f"{kind.value}: пустой список стирает и записи, и их элементы")
    pauses = {"anna": 1700000000, "tv": 1700000500}
    new_put(Kind.SUSPENDED, pauses)
    ok(new_get(Kind.SUSPENDED) == pauses and sql("SELECT user_name, since FROM suspended ORDER BY user_name") == [("anna", 1700000000), ("tv", 1700000500)], "паузы: записаны и прочитаны, в таблице — имя и время числом")
    new_put(Kind.SUSPENDED, {"max": 1700000900})
    ok(new_get(Kind.SUSPENDED) == {"max": 1700000900}, "паузы: записанное заменяет прежнее целиком")


def t_dns():
    print("\n— коды DNS")
    ready()
    no = refusal(new_get, Kind.DNS)
    ok(no == ("down", "коды DNS ещё не перенесены в базу (store-init)"), "пока коды не перенесены — «недоступно», а не «кодов нет»", no)
    ok(refusal(new_put, Kind.DNS, {})[0] == "down", "…и записать их тоже нельзя")
    sql("INSERT INTO meta (key, value) VALUES ('moved:dns', '1')")
    devices = {"k3x9q2w8e7r6": {"name": "tv_kitchen", "created": 1700000000}, "aaaa1111bbbb": {"name": "pad", "created": 1700000001}}
    new_put(Kind.DNS, devices)
    ok(new_get(Kind.DNS) == devices and sorted(sql("SELECT token, name, created_at FROM dns_devices")) == [("aaaa1111bbbb", "pad", 1700000001), ("k3x9q2w8e7r6", "tv_kitchen", 1700000000)],
       "записанное читается то же; в таблице — код, имя и время числом")
    new_put(Kind.DNS, {})
    ok(new_get(Kind.DNS) == {}, "стёртое читается пустым")


def t_refusals():
    print("\n— что не берут")
    ready()
    sql("INSERT INTO meta (key, value) VALUES ('moved:dns', '1')")
    for kind in ORDERED:
        answers = [refusal(new_put, kind, bad) for bad in BAD]
        words = [("refused", f"{kind.value}: не список")] * 3 + [("refused", f"{kind.value}: запись без своего id")] * (len(BAD) - 3)
        ok(answers == words, f"{kind.value}: негодные документы отклонены, и сказано чем", answers)
    for bad in (None, [], {"a": "1"}, {"a": 1.5}, {1: 1}, {"a": True}):
        if bad == {}:
            continue
        no = refusal(new_put, Kind.SUSPENDED, bad)
        ok(no == ("refused", "suspended: не {имя: время}"), f"паузы: {bad!r} отклонено", no)
    for bad in (None, {"": {"name": "a", "created": 1}}, {"t": {"name": "a"}}, {"t": {"name": "a", "created": "1"}}, {"t": {"name": "a", "created": 1}, "u": {"name": "a", "created": 2}}):
        no = refusal(new_put, Kind.DNS, bad)
        ok(no == ("refused", "dns: не {код: {name, created}} с разными именами"), f"коды DNS: {str(bad)[:40]} отклонено", no)
    ok(isinstance(StoreRefused("x"), ValueError), "отказ хранилища — по-прежнему ValueError")


def t_rows():
    print("\n— разбор документа на строки таблиц")
    rnd = random.Random(7)
    values = [None, True, False, 0, 1, 7, 2.5, "", "x", "имя", [], ["a"], ["a", 1], {"k": 1}]
    fields = ["name", "on", "items", "members", "space", "exit", "filter", "hash", "group", "left", "total", "expires_at", "created", "used", "auto", "url"]
    digest = hashlib.sha256()
    for n in range(4000):
        kind = rnd.choice([kind for kind in ORDERED if kind is not Kind.RULES])      # the rules came later: the kept digest is of the kinds before them
        doc = [dict({"id": f"r{n}x{i}"}, **{f: rnd.choice(values) for f in rnd.sample(fields, rnd.randint(0, 6))}) for i in range(rnd.randint(0, 4))]
        mine = [[r.id, r.position, list(r.typed), r.listed, r.extra, list(r.entries)] for r in documents.rows_of(kind, doc)]
        digest.update(json.dumps(mine, ensure_ascii=False, sort_keys=True).encode())
    ok(digest.hexdigest() == GOLDEN["rows_digest"], "4000 случайных документов разобраны на те же строки, что пишет база на сервере", digest.hexdigest())


def t_transaction():
    print("\n— одна операция — одна транзакция")
    ready()
    new_put(Kind.SPACES, ODD[Kind.SPACES])
    new_put(Kind.USER_GROUPS, ODD[Kind.USER_GROUPS])

    async def half():
        async with NEW.transaction() as tx:
            await tx.put(Kind.SPACES, [])
            await tx.put(Kind.USER_GROUPS, [])
            raise Refused("передумали")
    kind_of_no, _ = (lambda: ("refused", "") if isinstance(_try(half), Refused) else (None, ""))()
    ok(kind_of_no == "refused" and new_get(Kind.SPACES) == ODD[Kind.SPACES] and new_get(Kind.USER_GROUPS) == ODD[Kind.USER_GROUPS], "отказ посреди изменения двух видов — не сохранено ни одно")

    async def whole():
        async with NEW.transaction() as tx:
            await tx.put(Kind.SPACES, [])
            await tx.put(Kind.USER_GROUPS, [])
    run(whole())
    ok(new_get(Kind.SPACES) == [] and new_get(Kind.USER_GROUPS) == [], "без отказа — сохранены оба")

    async def meta():
        async with NEW.transaction() as tx:
            await tx.set_meta("cascade", "{}")
            await tx.mark("moved:cascade", "1")
            await tx.mark("moved:cascade", "2")
            return await tx.meta(["cascade", "moved:cascade", "nothing"])
    ok(run(meta()) == {"cascade": "{}", "moved:cascade": "1"} and sql("SELECT value FROM meta WHERE key = 'cascade'") == [("{}",)], "отдельные значения: запись, отметка «один раз», чтение")


def _try(work):
    try:
        run(work())
    except Exception as e:
        return e
    return None


def t_down():
    print("\n— база недоступна")
    away = Store("host=127.0.0.1 port=1 dbname=vpn user=vpn connect_timeout=2", PASSWORD_FILE)
    ok(isinstance(_try(lambda: away.get(Kind.SITES)), StoreDown) and isinstance(_try(lambda: away.put(Kind.SITES, [])), StoreDown), "нет связи с базой — «недоступно», а не пустой ответ")
    nowhere = Store(DSN, Path(TMP) / "no-password")
    ok(isinstance(_try(lambda: nowhere.get(Kind.SITES)), StoreDown), "нет файла с паролем — тоже «недоступно»")


def t_locks():
    print("\n— замок общий")
    ready()
    new_put(Kind.USER_GROUPS, [])
    lock = USERS_LOCK
    order = []

    def other_writer(record_id, name, pause):
        """Another writer beside the daemon (a shell command): a loop and a store of its own, the same lock file."""
        store = Store(DSN, PASSWORD_FILE)

        async def change():
            async with file_lock(lock):
                order.append("other in")
                async with store.transaction() as tx:
                    doc = await tx.document(Kind.USER_GROUPS)
                    await asyncio.sleep(pause)
                    await tx.put(Kind.USER_GROUPS, list(doc) + [{"id": record_id, "name": name, "members": []}])
                order.append("other out")
        asyncio.run(change())

    async def new_change():
        async with file_lock(lock):
            order.append("new in")
            async with NEW.transaction() as tx:
                doc = await tx.document(Kind.USER_GROUPS)
                await tx.put(Kind.USER_GROUPS, list(doc) + [{"id": "new001", "name": "из нового", "members": []}])
            order.append("new out")

    async def both():
        thread = threading.Thread(target=other_writer, args=("other1", "из другого", 0.6))
        thread.start()
        await asyncio.sleep(0.2)
        beat = asyncio.ensure_future(heartbeat())
        await new_change()
        ticks = await beat
        await asyncio.to_thread(thread.join)
        return ticks

    async def heartbeat():
        ticks = 0
        for _ in range(8):
            await asyncio.sleep(0.05)
            ticks += 1
        return ticks
    ticks = run(both())
    ids = sorted(g["id"] for g in new_get(Kind.USER_GROUPS))
    ok(order == ["other in", "other out", "new in", "new out"], "второй ждёт, пока первый держит замок", order)
    ok(ids == ["new001", "other1"], "оба изменения на месте: ни одно не затёрло другое", ids)
    ok(ticks == 8, "пока второй ждёт замок, цикл событий не стоит", ticks)

    def hold(seconds):
        async def holding():
            async with file_lock(lock):
                await asyncio.sleep(seconds)
        asyncio.run(holding())

    async def impatient():
        thread = threading.Thread(target=hold, args=(1.0,))
        thread.start()
        await asyncio.sleep(0.2)
        try:
            async with file_lock(lock, wait_seconds=0.3):
                return "got it"
        except Refused as e:
            return str(e)
        finally:
            await asyncio.to_thread(thread.join)
    ok(run(impatient()) == BUSY, "замок не освободился за отведённое время — отказ словами, которые видит панель")


def t_rules_moved():
    print("\n— правила: собраны из групп сайтов и блокировки, старые документы пишутся рядом")
    from awgscale.agent.block.store import StoredBlockGroups as Blocks
    from awgscale.agent.rules.store import RULES_MOVED_KEY, StoredRules
    from awgscale.agent.sites.models import SiteGroup
    from awgscale.agent.sites.store import StoredSites
    ready()
    sites = [{"id": "a00001", "name": "YouTube", "on": True, "items": ["youtube.com"]},
             {"id": "a00002", "name": "Банки", "on": True, "direct": True, "items": ["sber.ru"]},
             {"id": "a00003", "name": "Telegram", "on": False, "auto": "telegram", "items": ["t.me"]}]
    blocks = [{"id": "b00001", "name": "Свои", "on": True, "kind": "block", "items": ["ads.example"]}]
    new_put(Kind.SITES, sites)                          # as the earlier version left them
    new_put(Kind.BLOCK, blocks)
    ok(sql("SELECT count(*) FROM rules")[0][0] == 0 and [g.wire() for g in run(StoredSites(NEW).read())] == sites and run(Blocks(NEW).read()) == blocks,
       "до переноса: правил в базе нет, а «Сайты» и «Блокируется» читаются из того, что есть")
    run(StoredSites(NEW).save([SiteGroup.of({**sites[0], "on": False}), *[SiteGroup.of(g) for g in sites[1:]]]))
    rules = run(StoredRules(NEW).read())
    marked = sql("SELECT 1 FROM meta WHERE key = %s", RULES_MOVED_KEY)
    ok([(r["name"], r["route"], r["on"]) for r in rules] == [("YouTube", "exit", False), ("Банки", "direct", True), ("Telegram", "exit", False), ("Свои", "block", True)] and marked,
       "первое сохранение собрало правила (с переносом «напрямую» и блокировки) и отметило перенос", rules)
    ok(new_get(Kind.SITES)[0]["on"] is False and new_get(Kind.BLOCK) == blocks and new_get(Kind.SITES)[1].get("direct") is True,
       "старые документы записаны рядом: прежняя версия агента прочтёт то же", [new_get(Kind.SITES), new_get(Kind.BLOCK)])
    run(Blocks(NEW).save([{**blocks[0], "on": False}, {"id": "b00002", "name": "Ещё", "on": True, "kind": "block", "items": ["more.example"]}]))
    rules = run(StoredRules(NEW).read())
    ok([r["name"] for r in rules] == ["YouTube", "Банки", "Telegram", "Свои", "Ещё"] and rules[3]["on"] is False and [g["name"] for g in new_get(Kind.BLOCK)] == ["Свои", "Ещё"],
       "сохранение «Блокируется» — в свои места, сайты не тронуты", [r["name"] for r in rules])
    sites_now = [SiteGroup.of(g) for g in new_get(Kind.SITES)] + [SiteGroup.of({"id": "a00009", "name": "Сразу", "on": True, "items": ["at.once"]})]
    blocks_now = [*new_get(Kind.BLOCK), {"id": "b00009", "name": "Тоже сразу", "on": True, "kind": "block", "items": ["too.example"]}]

    async def both():
        await asyncio.gather(StoredSites(NEW).save(sites_now), Blocks(NEW).save(blocks_now))
    run(both())
    names = [r["name"] for r in run(StoredRules(NEW).read())]
    ok("Сразу" in names and "Тоже сразу" in names, "«Сайты» и «Блокируется» сохраняются одновременно: ни одно не затирает другое", names)

    whole = run(StoredRules(NEW).read())
    reordered = [dict(whole[-1], route="exit"), *whole[:-1]]
    run(StoredRules(NEW).save_all(reordered))
    ok([r["id"] for r in run(StoredRules(NEW).read())] == [r["id"] for r in reordered] and new_get(Kind.SITES)[0]["id"] == reordered[0]["id"]
       and reordered[0]["id"] not in [g["id"] for g in new_get(Kind.BLOCK)],
       "весь список записан в новом порядке (правило блокировки стало первым и «за границу»), старые документы — его виды", [r["name"] for r in run(StoredRules(NEW).read())])
    from awgscale.agent.rules.store import reconcile
    async def reconciled():
        async with NEW.transaction() as tx:
            return await reconcile(tx)
    ok(run(reconciled()) is False, "старые документы совпадают со списком: собирать заново нечего")
    old_sites = new_get(Kind.SITES)
    new_put(Kind.SITES, [*old_sites, {"id": "a00010", "name": "Из прежней версии", "on": True, "items": ["old.example"]}])     # an earlier agent, put back for a while
    ok(run(reconciled()) is True and "Из прежней версии" in [r["name"] for r in run(StoredRules(NEW).read())],
       "прежняя версия, возвращённая на время, меняла группы сайтов: при следующей выкатке список собран заново из них, ничего не потеряно")

    print("\n— правило «за границу» или «напрямую» для отдельных групп: только в списке, не в «Сайтах» и не в старых документах")
    from awgscale.agent.rules.store import Part, part_of, sites_of
    whole = run(StoredRules(NEW).read())
    scoped = {"id": "c00001", "name": "Детям напрямую", "on": True, "route": "direct", "groups": ["g00001"], "items": ["kids.example"]}
    with_scoped = [whole[0], scoped, *whole[1:]]
    run(StoredRules(NEW).save_all(with_scoped))
    ok(part_of(scoped) is None and part_of({**scoped, "route": "block"}) is Part.BLOCK and part_of({k: v for k, v in scoped.items() if k != "groups"}) is Part.SITES,
       "part_of: для групп «за границу»/«напрямую» — ни в одной части, блокировка для групп — в блокировке, без групп — в сайтах")
    ok("c00001" not in [g["id"] for g in new_get(Kind.SITES)] and "c00001" not in [g.id for g in run(StoredSites(NEW).read())]
       and "c00001" not in [g["id"] for g in new_get(Kind.BLOCK)],
       "в «Сайтах» и в документах прежней версии правила для групп нет: прежний агент ничего не направит для всех", [g["id"] for g in new_get(Kind.SITES)])
    sites_back = [SiteGroup.of(g) for g in new_get(Kind.SITES)]
    run(StoredSites(NEW).save([sites_back[0].model_copy(update={"on": not sites_back[0].on}), *sites_back[1:]]))
    after = run(StoredRules(NEW).read())
    ok([r["id"] for r in after] == [r["id"] for r in with_scoped] and next(r for r in after if r["id"] == "c00001") == scoped,
       "сохранение «Сайтов» вокруг правила для групп: оно стоит на своём месте, нетронутое", [r["id"] for r in after])
    new_put(Kind.SITES, [*new_get(Kind.SITES), {"id": "a00011", "name": "Ещё из прежней", "on": True, "items": ["older.example"]}])
    ok(run(reconciled()) is True and next(r for r in run(StoredRules(NEW).read()) if r["id"] == "c00001") == scoped
       and [r["id"] for r in run(StoredRules(NEW).read())].index("c00001") == 1,
       "прежняя версия сохранила «Сайты»: при выкатке список собран заново, правило для групп на своём месте")
    clash = [*sites_of(run(StoredRules(NEW).read())), {"id": "c00001", "name": "Не то", "on": True, "items": ["x.example"]}]
    try:
        run(StoredSites(NEW).save([SiteGroup.of(g) for g in clash]))
        ok(False, "группа сайтов с id правила для групп — отказ")
    except StoreRefused:
        ok(True, "группа сайтов с id правила для групп (сделаны врозь) — отказ, правило не затёрто")


def main():
    t_empty()
    t_roundtrip()
    t_rules_moved()
    t_dns()
    t_refusals()
    t_rows()
    t_transaction()
    t_down()
    t_locks()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
