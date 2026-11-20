// Подключение: the big button, where the traffic leaves, and how the group is protected.
import { useCallback, useEffect, useState } from "react";

import { backend, shownError } from "../backend";
import { type Destination, type Exit, Filter, type Me, type ProfileShown, TunnelState } from "../types";
import { PowerIcon } from "./icons";
import { Places } from "./Places";

const FILTER_NAMES: Record<Filter, string> = {
  [Filter.Standard]: "Обычная",
  [Filter.Off]: "Выключена",
  [Filter.Kids]: "Детская",
  [Filter.Pro]: "Pro",
  [Filter.ProPlus]: "Pro++",
  [Filter.Ultimate]: "Ultimate",
};

// The tunnel's state is asked again this often: the service or the server may have gone away.
const STATE_POLL_MILLISECONDS = 5000;

const STATE_TEXT: Record<TunnelState, string> = {
  [TunnelState.Down]: "Отключено",
  [TunnelState.Connecting]: "Подключаемся…",
  [TunnelState.Up]: "Подключено",
};

interface HomeProps {
  profile: ProfileShown;
  onOwner: (owner: boolean) => void;
}

function placeName(me: Me | null): string {
  if (me?.exit.pinned != null && me.exit.name != null) {
    return me.exit.name;
  }
  return "Авто";
}

function placeMeta(me: Me | null): string {
  if (me == null) {
    return "станет видно после подключения";
  }
  if (me.exit.pinned != null) {
    return "выход закреплён за вами";
  }
  if (me.exit.group != null && me.exit.name != null) {
    return `выход вашей группы · ${me.exit.name}`;
  }
  return "лучший сервер сейчас";
}

export function Home({ profile, onOwner }: HomeProps) {
  const [state, setState] = useState(TunnelState.Down);
  const [me, setMe] = useState<Me | null>(null);
  const [exits, setExits] = useState<Exit[]>([]);
  const [sheet, setSheet] = useState(false);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [mine, all] = await Promise.all([backend.me(), backend.exits()]);
      setMe(mine);
      setExits(all);
      onOwner(mine.owner);
      setError("");
    } catch (failure) {
      setError(shownError(failure));
    }
  }, [onOwner]);

  const ask = useCallback(() => {
    backend
      .tunnel()
      .then(report => {
        setState(current => (current === TunnelState.Connecting ? current : report.state));
        setWarning(report.warning);
      })
      .catch(() => setState(TunnelState.Down));
  }, []);

  useEffect(() => {
    const timer = window.setInterval(ask, STATE_POLL_MILLISECONDS);
    return () => window.clearInterval(timer);
  }, [ask]);

  useEffect(() => {
    ask();
    // the device may be inside the network already (another app holds the tunnel): then it is shown
    backend
      .me()
      .then(mine => {
        setMe(mine);
        onOwner(mine.owner);
        return backend.exits();
      })
      .then(setExits)
      .catch(() => undefined);
  }, [ask, onOwner]);

  useEffect(() => {
    if (state === TunnelState.Up) {
      void refresh();
    }
  }, [state, refresh]);

  async function toggle() {
    setError("");
    const wasUp = state === TunnelState.Up;
    setState(TunnelState.Connecting);
    try {
      setState(wasUp ? await backend.disconnect() : await backend.connect());
      ask();
    } catch (failure) {
      setError(shownError(failure));
      setState(
        await backend.tunnel().then(
          report => report.state,
          () => TunnelState.Down,
        ),
      );
    }
  }

  async function choose(destination: Destination) {
    setSheet(false);
    try {
      await backend.choose(destination);
      await refresh();
    } catch (failure) {
      setError(shownError(failure));
    }
  }

  const up = state === TunnelState.Up;
  const pinned = me?.exit.pinned ?? null;
  return (
    <main className="screen">
      <header className="head">
        <span>
          <h1>Моя сеть</h1>
          <span className="muted small">{profile.host}</span>
        </span>
        {me?.owner === true && <span className="badge">владелец</span>}
      </header>

      <div className="power-area">
        <button
          type="button"
          className={`power ${state}`}
          aria-pressed={up}
          aria-label={up ? "Отключить" : "Подключить"}
          onClick={() => void toggle()}
          disabled={state === TunnelState.Connecting}
        >
          <PowerIcon size={64} />
        </button>
        <div className={`state ${state}`}>{STATE_TEXT[state]}</div>
        <div className="mono muted small">{up ? profile.address : "нажмите, чтобы подключиться"}</div>
      </div>

      <div className="stack">
        <button type="button" className="place" onClick={() => setSheet(true)} disabled={me == null}>
          <span className="code">{pinned == null ? "A" : pinned}</span>
          <span className="grow">
            <span className="strong">{placeName(me)}</span>
            <span className="muted small">{placeMeta(me)}</span>
          </span>
          <span className="muted">›</span>
        </button>

        {me?.group != null && (
          <div className="card row">
            <span className="grow">
              <span className="small muted">Защита</span>
              <span className="strong">{FILTER_NAMES[me.group.filter]}</span>
            </span>
            <span className="muted small">задаёт владелец</span>
          </div>
        )}
      </div>

      {warning != null && up && <p className="note">{warning}</p>}

      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {sheet && (
        <Places
          exits={exits}
          pinned={pinned}
          onChoose={destination => void choose(destination)}
          onClose={() => setSheet(false)}
        />
      )}
    </main>
  );
}
