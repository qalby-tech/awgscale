#!/usr/bin/env python3
"""The journal of the agent (awgscale.agent.journal) and the validation of its
commands (awgscale.agent.commands): thousands of generated inputs give the
answers recorded in tests/golden/agent_journal.json.

    uv run python tests/agent_journal.py
    GOLDEN_WRITE=1 uv run python tests/agent_journal.py    (records the golden file anew)

One line per check, the last line is "N of M checks passed".
"""
import asyncio
import base64
import hashlib
import json
import os
import random
import socket
import sys
import tempfile
import time
from pathlib import Path

import httpx

from awgscale.agent import commands
from awgscale.agent.dispatcher import Dispatcher
from awgscale.agent.journal.models import Entry, Kind, Who
from awgscale.agent.journal.reader import JournalReader, entries_of
from awgscale.agent.journal.service import Journal
from awgscale.agent.journal.texts import describe
from awgscale.agent.journal.writer import JournalWriter, datagram
from awgscale.agent.shares import shares_percent

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="agent-journal-")
n = failed = 0


GOLDEN_FILE = os.path.join(HERE, "golden", "agent_journal.json")
WRITE = os.environ.get("GOLDEN_WRITE") == "1"
GOLDEN = {} if WRITE else json.load(open(GOLDEN_FILE))


def as_golden(key, value):
    """→ (the value as JSON keeps it, what the golden file has for it); GOLDEN_WRITE=1 records it instead."""
    value = json.loads(json.dumps(value, ensure_ascii=False))
    if WRITE:
        GOLDEN[key] = value
    return value, GOLDEN.get(key)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def ok(cond, what, got=None):
    global n, failed
    n += 1
    failed += not cond
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or got is None else f"   — got: {got!r}"[:700]))


def t_validation():
    print("\n— команды: проверка запроса")
    rng = random.Random(7)
    # the questions about the users at scale (clients, day, traffic) and the home country came after the recording: they are checked by name below, not drawn
    newer = {commands.Command.CLIENTS, commands.Command.DAY, commands.Command.TRAFFIC, commands.Command.COUNTRY_SET}
    names = [command.value for command in commands.Command if command not in newer] + ["bogus", "", "cascade-set", "store-init", "Invite-list"]
    pool = ["on", "off", "auto", "1", "12", "100", "0", "force", "reboot", "list", "geo", "none", "7d", "0d", "12h", "ann", "-ann", "a b", "x" * 33, "abc123", "ABC123",
            "a.example.com", "/path?x=1", "/pa th", "aGVsbG8=", "aGVs bG8=", "A" * 22, "A" * 21, "", "ann\n", "exit1-nl", "x" * 900, "QQ==", "example", "exa_mple.com", "имя"]
    cases = [[rng.choice(names)] + [rng.choice(pool) for _ in range(rng.choice([0, 0, 1, 1, 1, 2, 2, 3, 4]))] for _ in range(20000)]
    cases += [[], None, "state", ["state", 1], [["state"]], ("state",), {"0": "state"}, ["remove"] + ["ann"] * 60, ["remove"] + ["ann"] * 61, ["suspend", "on"] + ["a"] * 60,
              ["suspend", "on"] + ["a"] * 61, ["expire", "none"] + ["a"] * 60, ["expire", "none"] + ["a"] * 61, ["sites-set", "A" * 120000], ["sites-set", "A" * 120001],
              ["weights-set", "A" * 2000], ["weights-set", "A" * 2001], ["invite-add", "A" * 400], ["invite-add", "A" * 401], ["exit-add", "A" * 800], ["exit-add", "A" * 801],
              ["probe", "a.com", "/" + "a" * 600], ["probe", "a.com", "/" + "a" * 601]]
    ok(commands.parse(["clients", "A" * 8000]) is not None and commands.parse(["clients", "A" * 8001]) is None and commands.parse(["clients"]) is None,
       "clients: один запрос в base64, не длиннее 8000")
    ok(commands.parse(["day", "1763300000"]) is not None and commands.parse(["day", "0"]) is None and commands.parse(["day", "-1"]) is None and commands.parse(["day"]) is None,
       "day: начало суток в секундах")
    ok(commands.parse(["traffic"]) is not None and commands.parse(["traffic", "x"]) is None, "traffic: без аргументов")
    ok(commands.parse(["country-set", "kz"]) is not None and all(commands.parse(["country-set", wrong]) is None for wrong in ("KZ", "kaz", "k", "", "kz\n")) and commands.parse(["country-set"]) is None,
       "country-set: код страны из двух строчных латинских букв")
    passed = [commands.parse(case) is not None for case in cases]
    valid = [case for case, through in zip(cases, passed) if through]
    seen, expected = as_golden("validation", digest(passed))
    ok(seen == expected and len(valid) > 2000, f"{len(cases)} запросов (из них пропущено {len(valid)}): разбор пропускает ровно записанные", [seen, expected])
    valid += [["clients", "A" * 8000], ["day", "1763300000"], ["traffic"], ["country-set", "kz"]]
    ok(all(commands.parse(case).to_args() == case for case in valid), "пропущенный запрос передаётся команде теми же аргументами, что пришли")
    ok({command.value for command in commands.REQUESTS} == {case[0] for case in valid}, "у каждой команды сокета есть своя модель, и каждая встретилась в выборке",
       sorted({command.value for command in commands.REQUESTS} - {case[0] for case in valid}))


def t_shares():
    print("\n— доли выходов")
    rng = random.Random(3)
    cases = [[rng.randint(0, 99) for _ in range(rng.randint(0, 6))] for _ in range(5000)] + [[], [0], [0, 0], [1], [2, 1], [1, 1, 1], [67, 33], [1, 99]]
    seen, expected = as_golden("shares", digest([shares_percent(case) for case in cases]))
    ok(seen == expected and shares_percent([2, 1]) == [67, 33] and shares_percent([1, 1, 1]) == [34, 33, 33], f"{len(cases)} наборов весов: проценты — как записано", [seen, expected])


def t_texts():
    print("\n— журнал: что пишется о команде")
    rng = random.Random(11)
    people = ["ann", "bob", "carl", "dan", "eve", "fay", "gus", "hal", "ivy", "jon"]
    words = ["Дом", "Офис", "Дача", "Игры", "YouTube", "ИИ"]
    domains = ["youtube.com", "t.me", "openai.com", "*.perplexity.ai", "10.20.0.0/16", "notion.so", "canva.com"]

    def some(pool, most):
        return rng.sample(pool, rng.randint(0, min(most, len(pool))))

    def user_group(group_id):
        return {"id": group_id, "name": rng.choice(words), "space": rng.random() < 0.3, "exit": rng.choice([None, None, 1, 3]), "filter": rng.choice(["std", "kids", "off", None]), "members": some(people, 5)}

    def space(space_id):
        return {"id": space_id, "name": rng.choice(words), "on": rng.random() < 0.6, "members": some(people, 4)}

    def site_group(group_id):
        by_link = rng.random() < 0.25
        return {"id": group_id, "name": rng.choice(words), "on": rng.random() < 0.7, "items": some(domains, 6), **({"url": "https://example.org/list.txt"} if by_link else {})}

    def listing(make):
        return [make(group_id) for group_id in some(["aaaaaa", "bbbbbb", "cccccc", "dddddd", "eeeeee", "ffffff", "gggggg", "hhhhhh"], 8)]

    def exits():
        return {"exits": [{"name": f"exit{i}-nl", "weight": rng.choice([0, 1, 2, 5, None])} for i in range(rng.randint(0, 4))]}

    def case():
        command = rng.choice(["add", "remove", "expire", "suspend", "dns-add", "dns-remove", "block-set", "block-lists", "exit-add", "exit-attach", "exit-rename", "exit-remove",
                              "exit-update", "user-exit", "cascade-set", "weights-set", "mode-set", "spaces-set", "ugroups-set", "sites-set", "sites-refresh", "state", "qr"])
        arguments = [command] + {"add": [rng.choice(people)] + rng.choice([[], ["7d"]]), "expire": [rng.choice(["none", "12h"])] + some(people, 10) + ["zed"], "suspend": [rng.choice(["on", "off"])] + some(people, 10) + ["zed"],
                                 "user-exit": [rng.choice(["auto", "3"])] + some(people, 10) + ["zed"], "mode-set": [rng.choice(["list", "geo"])], "dns-add": ["tv"], "dns-remove": ["tv"]}.get(command, ["x"])
        reply = {"ok": rng.random() < 0.8}
        if rng.random() < 0.1:
            reply |= {"conflict": True, **({"error": "список изменился"} if rng.random() < 0.5 else {})}
        if rng.random() < 0.3:
            reply["already"] = True
        reply |= {"removed": some(people, 10), "name": "exit4-de", "address": "198.51.100.40", "was": "exit4", "reboot": rng.random() < 0.5, "exit_name": rng.choice([None, "exit2-nl"]), "cascade": exits()}
        before = None
        if command == "spaces-set":
            before = listing(space)
            reply["spaces"] = [dict(item, on=rng.random() < 0.5) for item in rng.sample(before, len(before) // 2)] + listing(space)[:2]
        if command == "ugroups-set":
            before = listing(user_group)
            reply["groups"] = [dict(item, name=rng.choice(words), members=some(people, 5), exit=rng.choice([None, 1]), filter=rng.choice(["std", "kids"]), space=rng.random() < 0.5) for item in rng.sample(before, len(before) // 2)] + listing(user_group)[:2]
        if command in ("sites-set", "sites-refresh"):
            before = listing(site_group)
            reply["groups"] = [dict(item, name=rng.choice(words), items=some(domains, 6), on=rng.random() < 0.5) for item in rng.sample(before, len(before) // 2)] + listing(site_group)[:2]
        if rng.random() < 0.1:
            before = None
        return arguments, before, reply
    cases = [case() for _ in range(20000)]
    described = [describe(arguments, before, reply) for arguments, before, reply in cases]
    written = {one[0] for one in described} - {None}
    seen, expected = as_golden("texts", digest(described))
    ok(seen == expected and len(written) > 3000, f"{len(cases)} ответов команд ({len(written)} разных записей): текст и пометка «плохая новость» — как записано", [seen, expected])
    ok(describe(["add", "ann", "7d"], None, {"ok": True}) == ("Создан пользователь ann, срок 7d", False) and describe(["remove", "ann"], None, {"ok": False, "removed": ["ann"]}) == ("Удалён пользователь ann", True)
       and describe(["weights-set", "x"], None, {"ok": True, "cascade": {"exits": [{"name": "a", "weight": 3}, {"name": "b", "weight": 1}]}}) == ("Доли выходов: a 75%, b 25%", False)
       and describe(["state"], None, {"ok": True}) == (None, False), "несколько записей дословно: создание, удаление, доли; чтение ничего не пишет")


def t_transport():
    print("\n— журнал: запись и чтение")
    path = os.path.join(TMP, "journald.sock")
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(path)
    receiver.settimeout(2)
    writer = JournalWriter(Path(path), "vpn-journal")
    sent = []
    same = True
    for line in ('{"t":1,"text":"Создан пользователь ann"}', "первая\nвторая", "", "x" * 3000):
        writer.send(line)
        got = receiver.recv(65536)
        same = same and got == datagram(line, "vpn-journal")
        sent.append(base64.b64encode(got).decode())
    seen, expected = as_golden("datagrams", sent)
    ok(same and seen == expected, "датаграмма в journald байт в байт записанная (в том числе значение с переводом строки)", [seen, expected])
    before = int(time.time())
    asyncio.run(Journal(None, writer).write("ж" * 900, True, Kind.EXIT, Who.BOT))
    sent = json.loads(receiver.recv(65536).split(b"\n")[0].split(b"=", 1)[1])
    ok(set(sent) == {"t", "text", "bad", "kind", "who"} and sent["text"] == "ж" * 500 and sent["bad"] is True and sent["kind"] == "exit" and sent["who"] == "bot" and before <= sent["t"] <= before + 5,
       "запись: t (секунды Unix), text (не длиннее 500), bad, kind, who — как прежде", sent)
    entry = Entry(at=__import__("datetime").datetime.fromtimestamp(1700000000, __import__("datetime").UTC), text="Проверка")
    ok(entry.line() == json.dumps({"t": 1700000000, "text": "Проверка", "bad": False, "kind": "change", "who": "system"}, ensure_ascii=False), "строка записи — тот же JSON, в том же порядке полей", entry.line())
    receiver.close()

    lines = [json.dumps({"t": 3, "text": "третья", "bad": False, "kind": "change", "who": "panel"}, ensure_ascii=False), "{оборванная", "[1, 2]", "5", '{"text": "без времени", "extra": {"a": 1}}',
             '{"t": 2.5, "text": "дробное время"}', '{"t": "строкой", "text": "время строкой"}', '{"t": null}']
    kept = []
    for line in lines:                       # what is shown: every line that is a JSON object
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            kept.append(item)
    ok(entries_of(lines) == kept and json.dumps(entries_of(lines)) == json.dumps(kept), "из строк Loki показываются записи как они есть, в том же виде; не-записи пропущены", entries_of(lines))

    asked = []

    def loki(request):
        asked.append(request)
        if loki.status != 200:
            return httpx.Response(loki.status, text="boom")
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
            {"stream": {"job": "vpn-journal"}, "values": [["3000000000", lines[0]], ["1000000000", '{"t": 1, "text": "первая"}']]},
            {"stream": {"job": "vpn-journal", "detected_level": "info"}, "values": [["4000000000", "{оборванная"], ["2000000000", '{"t": 2, "text": "вторая"}']]}]}})
    loki.status = 200
    journal = Journal(JournalReader("http://loki:3100", "vpn-journal", 400, httpx.MockTransport(loki)), writer)
    reply = asyncio.run(journal.show(commands.Journal(), None, None))
    query = dict(asked[-1].url.params)
    ok(reply["ok"] is True and [item["text"] for item in reply["items"]] == ["третья", "вторая", "первая"], "команда journal: несколько потоков сведены по времени, новые первыми", reply)
    ok(asked[-1].url.path == "/loki/api/v1/query_range" and query["query"] == '{job="vpn-journal"}' and query["direction"] == "backward" and query["limit"] == "300"
       and 399.9 < (int(query["end"]) - int(query["start"])) / 1e9 / 86400 < 400.1 and int(query["end"]) / 1e9 > time.time(), "запрос к Loki: поток vpn-journal, от новых к старым, 300 строк, окно 400 дней", query)
    loki.status = 500
    ok(asyncio.run(journal.show(commands.Journal(), None, None)) == {"ok": False, "error": "Журнал сейчас недоступен"}, "Loki не отвечает — отказ прежними словами, а не пустой журнал")


def t_dispatch():
    print("\n— диспетчер: команда journal выполняется новым кодом")
    os.environ.update({"VPN_LOKI_URL": "http://127.0.0.1:1"})
    from awgscale.agent.factory import get_dispatcher
    reply = asyncio.run(get_dispatcher().dispatch(["journal"]))
    ok(reply == {"ok": False, "error": "Журнал сейчас недоступен"}, "journal отвечает без прежнего файла агента вовсе (его здесь нет)", reply)
    ok(isinstance(get_dispatcher(), Dispatcher) and len(get_dispatcher().commands) == len(commands.Command), "обработчик есть у каждой команды сокета")


def main():
    t_validation()
    t_shares()
    t_texts()
    t_transport()
    t_dispatch()
    if WRITE:
        with open(GOLDEN_FILE, "w") as fh:
            json.dump(GOLDEN, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
    print(f"\n{n - failed} of {n} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
