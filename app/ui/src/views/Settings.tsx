// Настройки: what this device is in which network, and leaving it.
import { useState } from "react";

import { backend, shownError } from "../backend";
import { type ProfileShown } from "../types";

const VERSION = "0.1.0";

interface SettingsProps {
  profile: ProfileShown;
  owner: boolean;
  onLeft: () => void;
}

interface RowProps {
  label: string;
  value: string;
  hint?: string;
}

function Row({ label, value, hint }: RowProps) {
  return (
    <div className="setting">
      <span className="grow">
        <span>{label}</span>
        {hint != null && <span className="muted small">{hint}</span>}
      </span>
      <span className="muted mono">{value}</span>
    </div>
  );
}

export function Settings({ profile, owner, onLeft }: SettingsProps) {
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");

  async function leave() {
    try {
      await backend.leave();
      onLeft();
    } catch (failure) {
      setError(shownError(failure));
    }
  }

  return (
    <main className="screen">
      <header className="head">
        <h1>Настройки</h1>
      </header>
      <section className="group">
        <h2 className="small muted">Аккаунт</h2>
        <div className="card list">
          <Row label="Сеть" value={profile.host} hint={owner ? "вы владелец" : undefined} />
          <Row label="Это устройство" value={profile.name} />
          <Row label="Мой адрес в сети" value={profile.address} />
          {asking ? (
            <div className="setting">
              <span className="grow">Устройство забудет сеть; вернуться можно только по новой ссылке.</span>
              <button type="button" className="danger" onClick={() => void leave()}>
                Выйти
              </button>
              <button type="button" className="ghost" onClick={() => setAsking(false)}>
                Отмена
              </button>
            </div>
          ) : (
            <button type="button" className="setting danger-text" onClick={() => setAsking(true)}>
              Выйти из сети
            </button>
          )}
        </div>
      </section>
      <section className="group">
        <h2 className="small muted">О приложении</h2>
        <div className="card list">
          <Row label="Версия" value={VERSION} />
        </div>
      </section>
      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}
