// Подключение: the big button, where the traffic leaves, and how the person is protected.
import { useCallback, useEffect, useRef, useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import { AUTO_BADGE, badgeOf, currentExit, recentExits, rememberChoice, shownAddress } from "../locations";
import {
  type Destination,
  DestinationKind,
  type Exit,
  Filter,
  type FilterWish,
  FilterWishKind,
  type Me,
  NetworkKind,
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

// The location's badge while the exit the person keeps is not known yet: neither a country nor Авто
const UNKNOWN_BADGE = badgeOf(null);

interface HomeProps {
  profile: ProfileShown;
  onOwner: (owner: boolean) => void;
}

export function Home({ profile, onOwner }: HomeProps) {
  const texts = useTexts();
  const [state, setState] = useState(TunnelState.Down);
  const [me, setMe] = useState<Me | null>(null);
  const [exits, setExits] = useState<Exit[]>([]);
  const [locations, setLocations] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [error, setError] = useState("");
  // what went wrong with a choice of a location: shown only where it was made
  const [choiceError, setChoiceError] = useState("");
  const [warning, setWarning] = useState<string | null>(null);
  // a press that switches the tunnel off has not been answered yet
  const [disconnecting, setDisconnecting] = useState(false);
  // the number of the latest press, and how many presses are still waiting for their answer: only the latest
  // one's answer is shown, and the tunnel's own reports only when none is waiting
  const pressed = useRef(0);
  const waiting = useRef(0);
  // a connection given by hand is a tunnel only: there is no network to ask about the person or its exits
  const invited = profile.kind === NetworkKind.Invited;

  // Fails when the network does not answer
  const load = useCallback(async () => {
    const [mine, all] = await Promise.all([backend.me(), backend.exits()]);
    setMe(mine);
    setExits(all);
    onOwner(mine.owner);
  }, [onOwner]);

  const refresh = useCallback(async () => {
    try {
      await load();
      setError("");
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    }
  }, [load, texts]);

  const ask = useCallback(() => {
    backend
      .tunnel()
      .then(report => {
        // while the person's press is under way its answer decides; afterwards the tunnel's own report does,
        // so that a tunnel whose server went quiet shows it (and can still be switched off)
        setState(current => (waiting.current > 0 ? current : report.state));
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
    if (!invited) {
      return;
    }
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
  }, [ask, onOwner, invited]);

  useEffect(() => {
    if (state === TunnelState.Up && invited) {
      void refresh();
    }
  }, [state, refresh, invited]);

  // Only a tunnel that is down is switched on; connecting or up, a press switches it off (a press while it is
  // still connecting gives the connection up).
  async function toggle() {
    setError("");
    const connecting = state === TunnelState.Down;
    pressed.current += 1;
    const press = pressed.current;
    waiting.current += 1;
    if (connecting) {
      setState(TunnelState.Connecting);
    } else {
      setDisconnecting(true);
    }
    try {
      const next = connecting ? await backend.connect() : await backend.disconnect();
      if (press === pressed.current) {
        setState(next);
      }
    } catch (failure) {
      if (press === pressed.current) {
        setError(shownError(failure, texts.somethingWrong));
        setState(
          await backend.tunnel().then(
            report => report.state,
            () => TunnelState.Down,
          ),
        );
      }
    } finally {
      waiting.current -= 1;
      if (press === pressed.current) {
        setDisconnecting(false);
        ask();
      }
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

  // Says whether the network took the choice and the screens know it: when the location could not be asked
  // again afterwards the list stays open with what went wrong
  async function choose(destination: Destination): Promise<boolean> {
    setChoosing(true);
    setChoiceError("");
    try {
      await backend.choose(destination);
      if (destination.kind === DestinationKind.Exit) {
        rememberChoice(profile.host, destination.index);
      }
      await load();
      setError("");
      return true;
    } catch (failure) {
      setChoiceError(shownError(failure, texts.somethingWrong));
      return false;
    } finally {
      setChoosing(false);
    }
  }

  function showLocations(shown: boolean) {
    setChoiceError("");
    setLocations(shown);
  }

  const up = state === TunnelState.Up;
  const down = state === TunnelState.Down;
  const pinned = me?.exit.pinned ?? null;
  const current = currentExit(me?.exit ?? null, exits);
  // where the person is: the address they keep, their group's, or Авто; an exit's name only before the
  // exits are known
  let placeMeta = texts.auto;
  if (current != null) {
    placeMeta = pinned == null ? shownAddress(current) : `${shownAddress(current)} · ${texts.pinned}`;
  } else if (pinned != null && me?.exit.name != null) {
    placeMeta = `${me.exit.name} · ${texts.pinned}`;
  }
  let placeBadge = AUTO_BADGE;
  if (current != null) {
    placeBadge = badgeOf(current.country);
  } else if (pinned != null) {
    placeBadge = UNKNOWN_BADGE;
  }
  if (locations) {
    return (
      <Places
        exits={exits}
        current={current}
        pinned={pinned}
        recent={recentExits(profile.host, exits, current)}
        busy={choosing}
        error={choiceError}
        onChoose={choose}
        onForgetError={() => setChoiceError("")}
        onClose={() => showLocations(false)}
      />
    );
  }
  const filter = me?.filter ?? null;
  const route = me?.route ?? null;
  return (
    <main className="screen">
      <header className="head">
        <span>
          <h1>{invited ? texts.myNetwork : texts.manualNetwork}</h1>
          <span className="muted small">{profile.host}</span>
        </span>
        {me?.owner === true && <span className="badge">{texts.owner}</span>}
      </header>

      <div className="power-area">
        <button
          type="button"
          className={`power ${state}`}
          aria-pressed={!down}
          aria-label={down ? texts.connect : texts.disconnect}
          onClick={() => void toggle()}
          disabled={disconnecting}
        >
          <PowerIcon size={64} />
        </button>
        <div className={`state ${state}`}>{texts.states[state]}</div>
        {up && <div className="mono muted small">{profile.address}</div>}
      </div>

      <div className="stack">
        {invited && (
          <button type="button" className="place" onClick={() => showLocations(true)} disabled={me == null}>
            <span className="code">{placeBadge}</span>
            <span className="grow">
              <span className="strong">{texts.location}</span>
              <span className={current == null ? "muted small" : "muted small mono"}>{placeMeta}</span>
            </span>
            <span className="muted">›</span>
          </button>
        )}

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
    </main>
  );
}
