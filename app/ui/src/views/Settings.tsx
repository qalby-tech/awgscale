// Настройки: what this device is in which network, and leaving it.
import { type FormEvent, useEffect, useState } from "react";

import { backend, shownError } from "../backend";
import { type DeviceSettings, LOCAL_NETWORKS, type ProfileShown } from "../types";

const VERSION = "0.1.4";

interface SettingsProps {
  profile: ProfileShown;
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

interface ToggleProps {
  label: string;
  on: boolean;
  busy: boolean;
  onChange: (on: boolean) => void;
}

function Toggle({ label, on, busy, onChange }: ToggleProps) {
  return (
    <button
      type="button"
      className="setting"
      role="switch"
      aria-checked={on}
      disabled={busy}
      onClick={() => onChange(!on)}
    >
      <span className="grow">{label}</span>
      <span className={on ? "track on" : "track"}>
        <span className="knob" />
      </span>
    </button>
  );
}

export function Settings({ profile, onLeft }: SettingsProps) {
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState<DeviceSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [network, setNetwork] = useState("");

  useEffect(() => {
    backend
      .settings()
      .then(setChosen)
      .catch(() => undefined);
  }, []);

  async function save(next: DeviceSettings) {
    setSaving(true);
    setError("");
    try {
      setChosen(await backend.setSettings(next));
      setNetwork("");
    } catch (failure) {
      setError(shownError(failure));
    } finally {
      setSaving(false);
    }
  }

  function addNetwork(event: FormEvent) {
    event.preventDefault();
    if (chosen != null && network.trim().length > 0) {
      void save({ ...chosen, around: [...chosen.around, network.trim()] });
    }
  }

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
      {chosen != null && (
        <section className="group">
          <h2 className="small muted">Устройство</h2>
          <div className="card list">
            <Toggle
              label="Без VPN нет интернета"
              on={chosen.kill_switch}
              busy={saving}
              onChange={on => void save({ ...chosen, kill_switch: on })}
            />
            <Toggle
              label="Локальные сети мимо VPN"
              on={chosen.local_around}
              busy={saving}
              onChange={on => void save({ ...chosen, local_around: on })}
            />
          </div>
          <h2 className="small muted">Мимо VPN</h2>
          <div className="card list">
            {chosen.local_around &&
              LOCAL_NETWORKS.map(local => (
                <div className="setting" key={local.name}>
                  <span className="grow nowrap">{local.name}</span>
                  <span className="mono muted small">{local.networks}</span>
                  <span className="tag">авто</span>
                </div>
              ))}
            {chosen.around.map(item => (
              <div className="setting" key={item}>
                <span className="grow mono">{item}</span>
                <button
                  type="button"
                  className="close"
                  aria-label={`Убрать ${item}`}
                  disabled={saving}
                  onClick={() => void save({ ...chosen, around: chosen.around.filter(other => other !== item) })}
                >
                  ×
                </button>
              </div>
            ))}
            <form className="setting" onSubmit={addNetwork}>
              <input
                className="inline"
                value={network}
                onChange={event => setNetwork(event.target.value)}
                placeholder="10.96.0.0/12"
                autoComplete="off"
                spellCheck={false}
              />
              <button type="submit" className="ghost" disabled={saving || network.trim().length === 0}>
                Добавить
              </button>
            </form>
          </div>
        </section>
      )}
      <section className="group">
        <h2 className="small muted">Аккаунт</h2>
        <div className="card list">
          <Row label="Сеть" value={profile.host} />
          <Row label="Это устройство" value={profile.name} />
          <Row label="Мой адрес в сети" value={profile.address} />
          {asking ? (
            <div className="setting">
              <span className="grow">Выйти из сети?</span>
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
