"use client";

/** The shares of the exits: a percent for every exit, saved when they add up to 100. With two exits the other one follows by itself. */
import { useEffect, useRef, useState, type FormEvent } from "react";

import type { Exit } from "@/lib/types";
import { exitCall, openShares, sharesOf } from "@/store/exits";
import { fail } from "@/ui/toast";

const WHOLE = 100;
const MIN_SHARE = 1;
const MAX_SHARE = WHOLE - MIN_SHARE;

const isShare = (value: number): boolean => Number.isInteger(value) && value >= MIN_SHARE;

/** `exits` as they were when the editor opened: polls do not move the numbers under the cursor. */
export function SharesEditor({ exits }: { exits: Exit[] }) {
  const [base] = useState(exits);
  const [values, setValues] = useState(() => sharesOf(exits).map(String));
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    first.current?.focus();
    first.current?.select();
  }, []);

  const numbers = values.map(Number);
  const total = numbers.reduce((sum, value) => sum + (value || 0), 0);
  const good = total === WHOLE && numbers.every(isShare);

  const change = (index: number, text: string) =>
    setValues(current => {
      const next = current.slice();
      next[index] = text;
      const value = Number(text);
      if (next.length === 2 && value >= MIN_SHARE && value <= MAX_SHARE) next[1 - index] = String(WHOLE - value);
      return next;
    });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const weights = Object.fromEntries(base.map((exit, index) => [exit.index, numbers[index]]));
    const shown = Object.fromEntries(base.map(exit => [exit.index, exit.weight || 1])); // what the page showed: the server refuses a change made on older numbers
    try {
      await exitCall("/api/exits/weights", { weights, base: shown }, "Доли сохранены");
      openShares(false);
    } catch (error) {
      fail(error);
    }
  };

  return (
    <form className="exit-form shares" onSubmit={submit}>
      <h2 className="card-title">Доли выходов</h2>
      {base.map((exit, index) => (
        <label key={exit.index} className="share-row">
          <span>{exit.name}</span>
          <input
            ref={index ? undefined : first}
            className="input"
            type="number"
            min={MIN_SHARE}
            max={MAX_SHARE}
            step={1}
            value={values[index]}
            aria-label={"Доля " + exit.name + ", %"}
            data-i={exit.index}
            onChange={event => change(index, event.target.value)}
          />
          <span>%</span>
        </label>
      ))}
      <button type="submit" className="btn primary" disabled={!good}>
        Сохранить
      </button>
      <button type="button" className="btn" onClick={() => openShares(false)}>
        Отмена
      </button>
      <span className="hint">{good ? "в сумме 100%" : "в сумме " + total + "% — должно быть 100%"}</span>
      <div className="hint">
        Какая часть пользователей идёт через каждый выход. Пользователи распределяются заново сразу; открытые соединения
        остаются на прежнем выходе.
      </div>
    </form>
  );
}
