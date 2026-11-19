"use client";

/** The journal: what was changed and when, newest first. */
import { useEffect, useState } from "react";

import { api, isAuthError } from "@/lib/api";
import { whenText } from "@/lib/format";
import { useCore } from "@/store/core";
import { Dot, DotKind } from "@/ui/controls";
import { PageHead } from "@/ui/PageHead";
import { fail } from "@/ui/toast";

/** Who made a change. */
enum Author {
  Panel = "panel",
  Bot = "bot",
  System = "system",
  Invite = "invite",
}

const AUTHORS: Record<Author, string> = {
  [Author.Panel]: "Панель",
  [Author.Bot]: "Бот",
  [Author.System]: "Система",
  [Author.Invite]: "Приглашение",
};

interface JournalItem {
  t: number;
  text: string;
  bad?: boolean;
  who?: Author;
}

/** The journal is re-read with every third answer of the state poll. */
const RELOAD_EVERY_TICKS = 3;

function Row({ item }: { item: JournalItem }) {
  return (
    <tr>
      <td className="muted c-when">{whenText(item.t * 1000)}</td>
      <td className="muted c-who">{(item.who && AUTHORS[item.who]) || ""}</td>
      <td className={item.bad ? "c-bad" : ""}>
        {item.bad ? <Dot kind={DotKind.Crit} /> : null}
        <span>{item.text}</span>
      </td>
    </tr>
  );
}

const Note = ({ text }: { text: string }) => (
  <tr>
    <td className="muted">{text}</td>
  </tr>
);

/** The lines of the journal, or a note where there are none. */
function Rows({ items, failed }: { items: JournalItem[] | null; failed: boolean }) {
  if (!items) return failed ? <Note text="Нет данных" /> : null;
  if (!items.length) return <Note text="Изменений пока не было" />;
  return (
    <>
      {items.map((item, index) => (
        <Row key={items.length - index} item={item} />
      ))}
    </>
  );
}

export function JournalPage() {
  const tick = useCore(state => state.tick);
  const [items, setItems] = useState<JournalItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [opened] = useState(tick);

  useEffect(() => {
    if ((tick - opened) % RELOAD_EVERY_TICKS) return;
    let alive = true;
    api<{ items: JournalItem[] }>("/api/journal").then(
      data => {
        if (alive) setItems(data.items);
      },
      error => {
        if (isAuthError(error)) fail(error);
        if (alive) setFailed(true);
      },
    ); // what was read stays on the screen
    return () => {
      alive = false;
    };
  }, [tick, opened]);

  return (
    <>
      <PageHead title="Журнал" />
      <section className="card table-card" aria-label="Журнал изменений">
        <div className="table-wrap">
          <table className="plain t-journal">
            <tbody id="jrBody">
              <Rows items={items} failed={failed} />
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
