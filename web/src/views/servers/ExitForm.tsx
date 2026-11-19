"use client";

/** A rented server becomes a new exit: its address, a name, the root password (used once) and its share. */
import { useEffect, useRef, type FormEvent } from "react";

import { exitCall, openExitForm } from "@/store/exits";
import { Icon, IconName } from "@/ui/Icon";
import { fail } from "@/ui/toast";

const NAME_MAX = 32;
const MIN_SHARE = 1;
const MAX_SHARE = 99;

interface AddExitRequest {
  address: string;
  name: string;
  weight: number;
  password?: string;
}

export function AddExitButton({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className="btn" id="exitAddBtn" onClick={onOpen}>
      <Icon name={IconName.Plus} size={16} stroke={2} />
      Добавить выход
    </button>
  );
}

interface ExitFormProps {
  /** How many exits there are now: the new one is offered an equal share. */
  exitCount: number;
  /** Opened by its own button (not from the map): the cursor goes to the address. */
  focus: boolean;
}

export function ExitForm({ exitCount, focus }: ExitFormProps) {
  const address = useRef<HTMLInputElement>(null);
  const name = useRef<HTMLInputElement>(null);
  const password = useRef<HTMLInputElement>(null);
  const share = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (focus) address.current?.focus();
  }, [focus]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const passwordField = password.current;
    const body: AddExitRequest = {
      address: address.current?.value.trim() ?? "",
      name: name.current?.value.trim() ?? "",
      weight: Number(share.current?.value),
      ...(passwordField?.value ? { password: passwordField.value } : {}),
    };
    if (passwordField) passwordField.value = ""; // not kept in the page a moment longer than the request needs it
    try {
      await exitCall("/api/exits/add", body, body.name + ": подключение начато");
      openExitForm(false);
    } catch (error) {
      fail(error);
    }
  };

  return (
    <form className="exit-form" onSubmit={submit}>
      <input
        ref={address}
        className="input code"
        id="exitAddr"
        placeholder="Адрес сервера, например 203.0.113.10"
        inputMode="decimal"
        autoComplete="off"
        required
      />
      <input
        ref={name}
        className="input"
        id="exitName"
        placeholder="Имя, например exit3-de"
        autoComplete="off"
        maxLength={NAME_MAX}
        required
      />
      <input
        ref={password}
        className="input"
        id="exitPw"
        type="password"
        placeholder="Пароль root"
        autoComplete="new-password"
      />
      <label className="share-row">
        <span>доля</span>
        <input
          ref={share}
          className="input share-in"
          id="exitShare"
          type="number"
          min={MIN_SHARE}
          max={MAX_SHARE}
          step={1}
          defaultValue={Math.min(MAX_SHARE, Math.round(100 / (exitCount + 1)))}
          aria-label="Доля нового выхода, %"
          title="Доля нового выхода, %"
        />
        <span>%</span>
      </label>
      <button type="submit" className="btn primary">
        Подключить
      </button>
      <button type="button" className="btn" onClick={() => openExitForm(false)}>
        Отмена
      </button>
      <div className="hint">
        Нужен свежий сервер с Ubuntu 24.04 или новее и входом root. Пароль используется один раз, чтобы поставить ключ,
        и нигде не сохраняется. Без пароля — если ключ входа уже добавлен на сервер вручную. Доля — какую часть
        пользователей получит новый выход; остальные выходы поделят оставшееся в прежней пропорции.
      </div>
    </form>
  );
}
