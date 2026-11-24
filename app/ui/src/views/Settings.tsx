// Настройки: the device's choices, which network it is in, signing out of it, the language.
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { backend, shownError } from "../backend";
import { Language, LANGUAGE_NAMES, languageOf, useTexts } from "../i18n";
import { type DeviceSettings, LOCAL_NETWORKS, NetworkKind, type ProfileShown } from "../types";

const VERSION = "0.1.13";

interface SettingsProps {
  profile: ProfileShown;
  onLeft: () => void;
  onLanguage: (language: Language) => void;
}

interface RowProps {
  label: string;
  value: string;
}

function Row({ label, value }: RowProps) {
  return (
    <div className="setting">
      <span className="grow">{label}</span>
      <span className="muted mono">{value}</span>
    </div>
  );
}

interface ToggleProps {
  label: string;
  // what it does, under its name
  note: string;
  on: boolean;
  busy: boolean;
  onChange: (on: boolean) => void;
}

// A switch named by its label alone; what it does is its description
function Toggle({ label, note, on, busy, onChange }: ToggleProps) {
  const noteId = useId();
  return (
    <button
      type="button"
      className="setting"
      role="switch"
      aria-checked={on}
      aria-label={label}
      aria-describedby={noteId}
      disabled={busy}
      onClick={() => onChange(!on)}
    >
      <span className="grow">
        <span className="label">{label}</span>
        <span className="muted small" id={noteId}>
          {note}
        </span>
      </span>
      <span className={on ? "track on" : "track"}>
        <span className="knob" />
      </span>
    </button>
  );
}

export function Settings({ profile, onLeft, onLanguage }: SettingsProps) {
  const texts = useTexts();
  // a connection given by hand is removed, not signed out of; it has a server, not a network with a user of
  // this device, unless its server turned out to be an awgscale network (then it is shown as one)
  const invited = profile.kind === NetworkKind.Invited;
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState<DeviceSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [network, setNetwork] = useState("");
  // the question of signing out, brought into view when asked (it is the page's last row)
  const question = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (asking) {
      question.current?.scrollIntoView({ block: "nearest" });
    }
  }, [asking]);

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
      setError(shownError(failure, texts.somethingWrong));
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

  function speak(language: Language) {
    if (chosen != null) {
      onLanguage(language);
      void save({ ...chosen, language });
    }
  }

  // This device forgets the network and brings its tunnel down; the network keeps the device (the app API
  // has no way for a device to remove itself), so the owner removes it in the panel
  async function signOut() {
    try {
      await backend.leave();
      onLeft();
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    }
  }

  return (
    <main className="screen">
      <header className="head">
        <h1>{texts.settings}</h1>
      </header>
      {chosen != null && (
        <section className="group">
          <h2 className="small muted">{texts.device}</h2>
          <div className="card list">
            <Toggle
              label={texts.killSwitch}
              note={texts.killSwitchNote}
              on={chosen.kill_switch}
              busy={saving}
              onChange={on => void save({ ...chosen, kill_switch: on })}
            />
          </div>
          <h2 className="small muted">{texts.splitTunneling}</h2>
          <div className="card list">
            <Toggle
              label={texts.localNetworks}
              note={texts.localNetworksNote}
              on={chosen.local_around}
              busy={saving}
              onChange={on => void save({ ...chosen, local_around: on })}
            />
            {chosen.local_around &&
              LOCAL_NETWORKS.map(local => (
                <div className="setting" key={local.kind}>
                  <span className="nowrap">{texts.localNames[local.kind]}</span>
                  <span className="mono muted small networks">{local.networks}</span>
                  <span className="tag">{texts.autoTag}</span>
                </div>
              ))}
            {chosen.around.map(item => (
              <div className="setting" key={item}>
                <span className="grow mono">{item}</span>
                <button
                  type="button"
                  className="close"
                  aria-label={texts.remove(item)}
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
                {texts.add}
              </button>
            </form>
          </div>
        </section>
      )}
      <section className="group">
        <h2 className="small muted">{texts.account}</h2>
        <div className="card list">
          {profile.panel ? (
            <>
              <Row label={texts.network} value={profile.host} />
              <Row label={texts.thisDevice} value={profile.name} />
            </>
          ) : (
            <>
              <Row label={texts.manualNetwork} value={profile.name} />
              <Row label={texts.server} value={profile.host} />
            </>
          )}
          <Row label={texts.myAddress} value={profile.address} />
          {asking ? (
            <div className="setting confirm" ref={question}>
              <span className="grow">
                <span>{invited ? texts.signOutQuestion : texts.removeQuestion}</span>
                {invited && <span className="muted small">{texts.signOutNote}</span>}
              </span>
              <button type="button" className="danger" onClick={() => void signOut()}>
                {invited ? texts.signOut : texts.removeYes}
              </button>
              <button type="button" className="ghost" onClick={() => setAsking(false)}>
                {texts.cancel}
              </button>
            </div>
          ) : (
            <button type="button" className="setting danger-text" onClick={() => setAsking(true)}>
              {invited ? texts.signOut : texts.removeConnection}
            </button>
          )}
        </div>
      </section>
      <section className="group">
        <h2 className="small muted">{texts.about}</h2>
        <div className="card list">
          <div className="setting">
            <span className="grow">{texts.language}</span>
            <div className="chips" role="group" aria-label={texts.language}>
              {Object.values(Language).map(language => (
                <button
                  key={language}
                  type="button"
                  className="chip"
                  aria-pressed={chosen != null && languageOf(chosen.language) === language}
                  disabled={chosen == null || saving}
                  onClick={() => speak(language)}
                >
                  {LANGUAGE_NAMES[language]}
                </button>
              ))}
            </div>
          </div>
          <Row label={texts.version} value={VERSION} />
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
