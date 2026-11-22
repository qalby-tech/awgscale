// Подключение: the big button, where the traffic leaves, and how the person is protected.
import { useCallback, useEffect, useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import {
  type Destination,
  type Exit,
  Filter,
  type FilterWish,
  FilterWishKind,
  type Me,
  type ProfileShown,
  Route,
  TunnelState,
} from "../types";
import { PowerIcon } from "./icons";
import { Places } from "./Places";

// Where the traffic may go, and the levels a person may choose for themselves, in the order they are offered
const ROUTES = [Route.Smart, Route.All];
const OWN_LEVELS = [Filter.Standard, Filter.Pro, Filter.ProPlus, Filter.Ultimate, Filter.Kids];

// The tunnel's state is asked again this often: the service or the server may have gone away.
const STATE_POLL_MILLISECONDS = 5000;

interface HomeProps {
  profile: ProfileShown;
  onOwner: (owner: boolean) => void;
}

export function Home({ profile, onOwner }: HomeProps) {
  const texts = useTexts();
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
      setError(shownError(failure, texts.somethingWrong));
    }
  }, [onOwner, texts]);

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
      setError(shownError(failure, texts.somethingWrong));
      setState(
        await backend.tunnel().then(
          report => report.state,
          () => TunnelState.Down,
        ),
      );
    }
  }

  async function protect(wish: FilterWish) {
    try {
      await backend.chooseFilter(wish);
      await refresh();
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    }
  }

  async function steer(route: Route) {
    try {
      await backend.chooseRoute(route);
      await refresh();
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    }
  }

  async function choose(destination: Destination) {
    setSheet(false);
    try {
      await backend.choose(destination);
      await refresh();
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    }
  }

  const up = state === TunnelState.Up;
  const pinned = me?.exit.pinned ?? null;
  const placeName = pinned != null && me?.exit.name != null ? me.exit.name : texts.auto;
  let placeMeta = "";
  if (pinned != null) {
    placeMeta = texts.pinned;
  } else if (me?.exit.group != null && me.exit.name != null) {
    placeMeta = me.exit.name;
  }
  const filter = me?.filter ?? null;
  const route = me?.route ?? null;
  return (
    <main className="screen">
      <header className="head">
        <span>
          <h1>{texts.myNetwork}</h1>
          <span className="muted small">{profile.host}</span>
        </span>
        {me?.owner === true && <span className="badge">{texts.owner}</span>}
      </header>

      <div className="power-area">
        <button
          type="button"
          className={`power ${state}`}
          aria-pressed={up}
          aria-label={up ? texts.disconnect : texts.connect}
          onClick={() => void toggle()}
          disabled={state === TunnelState.Connecting}
        >
          <PowerIcon size={64} />
        </button>
        <div className={`state ${state}`}>{texts.states[state]}</div>
        {up && <div className="mono muted small">{profile.address}</div>}
      </div>

      <div className="stack">
        <button type="button" className="place" onClick={() => setSheet(true)} disabled={me == null}>
          <span className="code">{pinned == null ? "A" : pinned}</span>
          <span className="grow">
            <span className="strong">{placeName}</span>
            {placeMeta.length > 0 && <span className="muted small">{placeMeta}</span>}
          </span>
          <span className="muted">›</span>
        </button>

        {route != null && route.available && (
          <div className="group">
            <span className="small muted">{texts.route}</span>
            <div className="chips" role="group" aria-label={texts.route}>
              {ROUTES.map(choice => (
                <button
                  key={choice}
                  type="button"
                  className="chip"
                  aria-pressed={route.all === (choice === Route.All)}
                  onClick={() => void steer(choice)}
                >
                  {texts.routes[choice]}
                </button>
              ))}
            </div>
          </div>
        )}
        {filter != null && filter.locked && (
          <div className="card row">
            <span className="grow">
              <span className="small muted">{texts.protection}</span>
              <span className="strong">{texts.filters[filter.applies]}</span>
            </span>
            <span className="muted small">{texts.setByAdult}</span>
          </div>
        )}
        {filter != null && !filter.locked && (
          <div className="group">
            <span className="small muted">{texts.protection}</span>
            <div className="chips" role="group" aria-label={texts.protection}>
              <button
                type="button"
                className="chip"
                aria-pressed={filter.own == null}
                onClick={() => void protect({ kind: FilterWishKind.Auto })}
              >
                {me?.group == null ? texts.asGroup : `${texts.asGroup} · ${texts.filters[me.group.filter]}`}
              </button>
              {OWN_LEVELS.map(level => (
                <button
                  key={level}
                  type="button"
                  className="chip"
                  aria-pressed={filter.own === level}
                  onClick={() => void protect({ kind: FilterWishKind.Level, filter: level })}
                >
                  {texts.filters[level]}
                </button>
              ))}
            </div>
          </div>
        )}
        {filter == null && me?.group != null && (
          <div className="card row">
            <span className="grow">
              <span className="small muted">{texts.protection}</span>
              <span className="strong">{texts.filters[me.group.filter]}</span>
            </span>
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
