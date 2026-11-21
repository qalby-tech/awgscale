// First run: the link the owner (or the server's install) showed, then a name for this device.
import { type FormEvent, useState } from "react";

import { backend, shownError } from "../backend";
import { type InvitationInfo, type ProfileShown } from "../types";
import { Logo } from "./icons";

const DEVICE_NAME_PATTERN = "[A-Za-z0-9_][A-Za-z0-9_\\-]{0,31}";

interface StartProps {
  onJoined: (profile: ProfileShown) => void;
}

function invitationText(info: InvitationInfo): string {
  const group = info.group == null ? "" : ` · группа «${info.group}»`;
  return `Ссылка работает ещё для ${info.left} из ${info.total} устройств${group}`;
}

export function Start({ onJoined }: StartProps) {
  const [link, setLink] = useState("");
  const [info, setInfo] = useState<InvitationInfo | null>(null);
  const [device, setDevice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function look(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setInfo(await backend.look(link));
    } catch (failure) {
      setError(shownError(failure));
    } finally {
      setBusy(false);
    }
  }

  async function join(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onJoined(await backend.join(link, device));
    } catch (failure) {
      setError(shownError(failure));
      setBusy(false);
    }
  }

  function another() {
    setInfo(null);
    setError("");
  }

  return (
    <main className="screen start">
      <div className="brand">
        <Logo />
        <span>awgscale</span>
      </div>
      <div>
        <h1>Подключитесь к своей сети</h1>
      </div>
      {info == null ? (
        <form className="stack" onSubmit={look}>
          <label className="field">
            <span>Ссылка-приглашение</span>
            <input
              value={link}
              onChange={event => setLink(event.target.value)}
              placeholder="awgscale://join?…"
              autoComplete="off"
              spellCheck={false}
              required
            />
          </label>
          <button className="primary" type="submit" disabled={busy || link.trim().length === 0}>
            {busy ? "Проверяем…" : "Продолжить"}
          </button>
        </form>
      ) : (
        <form className="stack" onSubmit={join}>
          <p className="note">{invitationText(info)}</p>
          <label className="field">
            <span>Как назвать это устройство</span>
            <input
              value={device}
              onChange={event => setDevice(event.target.value)}
              placeholder="anna_phone"
              pattern={DEVICE_NAME_PATTERN}
              title="Латинские буквы, цифры, _ и -, до 32 символов"
              autoComplete="off"
              spellCheck={false}
              required
            />
          </label>
          <button className="primary" type="submit" disabled={busy || device.length === 0}>
            {busy ? "Создаём подключение…" : "Подключить устройство"}
          </button>
          <button className="ghost" type="button" onClick={another} disabled={busy}>
            Другая ссылка
          </button>
        </form>
      )}
      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}
