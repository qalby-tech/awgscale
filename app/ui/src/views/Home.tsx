// Подключение: the big button, where the traffic leaves, and how the person is protected.
import { useCallback, useEffect, useRef, useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import { AUTO_BADGE, currentExit, recentExits, rememberChoice, shownAddress } from "../locations";
import { LatestChoice, LEVELS, withLevel } from "../protection";
import {
  type Destination,
  DestinationKind,
  type Exit,
  type Filter,
  FilterWishKind,
  type Me,
  NetworkKind,
  type ProfileShown,
  Route,
  TunnelState,
} from "../types";
import { Flag, InfoIcon, PowerIcon } from "./icons";
import { Places } from "./Places";

// Where the traffic may go, in the order it is offered
const ROUTES = [Route.Smart, Route.All];

// The tunnel's state is asked again this often: the service or the server may have gone away.
const STATE_POLL_MILLISECONDS = 5000;

interface ProtectionTitleProps {
  onExplain: () => void;
}

// «Защита» and the button that explains its levels
function ProtectionTitle({ onExplain }: ProtectionTitleProps) {
  const texts = useTexts();
  return (
    <span className="row titled">
      <span className="small muted">{texts.protection}</span>
      <button type="button" className="info" aria-label={texts.aboutProtection} onClick={onExplain}>
        <InfoIcon />
      </button>
    </span>
  );
}

// Each level in one short line
function ProtectionLevels({ onClose }: { onClose: () => void }) {
  const texts = useTexts();
  return (
    <>
      <div className="dim" onClick={onClose} />
      <section className="sheet" role="dialog" aria-label={texts.protectionLevels}>
        <span className="grip" />
        <h2>{texts.protectionLevels}</h2>
        <ul className="levels">
          {LEVELS.map(level => (
            <li key={level}>
              <span className="strong">{texts.filters[level]}</span> — {texts.levelNotes[level]}
            </li>
          ))}
        </ul>
        <button type="button" className="ghost" onClick={onClose}>
          {texts.close}
        </button>
      </section>
    </>
  );
}

interface HomeProps {
  profile: ProfileShown;
  // the tab is in front: kept mounted behind the others, it asks the network again whenever it comes back
  shown: boolean;
  onOwner: (owner: boolean) => void;
}

export function Home({ profile, shown, onOwner }: HomeProps) {
  const texts = useTexts();
  const [state, setState] = useState(TunnelState.Down);
  const [me, setMe] = useState<Me | null>(null);
  const [exits, setExits] = useState<Exit[]>([]);
  const [locations, setLocations] = useState(false);
  const [explaining, setExplaining] = useState(false);
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
  // the level the person chose last while the network has not answered yet: it is the one shown at once
  const [wished, setWished] = useState<Filter | null>(null);
  // the levels go to the network one at a time, and only the latest of those chosen meanwhile
  const [levels] = useState(
    () => new LatestChoice<Filter>(level => backend.chooseFilter({ kind: FilterWishKind.Level, filter: level })),
  );
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

  // When the tab comes to the front (and when the app starts): what is shown stays as it was while the
  // network is asked again. The device may be inside the network already (another app holds the tunnel):
  // then it is shown; when the network does not answer nothing changes.
  useEffect(() => {
    if (!shown) {
      return;
    }
    ask();
    if (!invited) {
      return;
    }
    backend
      .me()
      .then(mine => {
        setMe(mine);
        onOwner(mine.owner);
        return backend.exits();
      })
      .then(setExits)
      .catch(() => undefined);
  }, [ask, onOwner, invited, shown]);

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

  // Only a level is sent, never "auto" (as the group): the app offers no way back to the group's level. The
  // chip is marked at once and the network asked in the background; the last of quick presses wins, and when
  // the network refuses it the chip goes back to the level the network holds.
  async function protect(level: Filter) {
    setWished(level);
    setError("");
    const settled = await levels.choose(level);
    if (settled == null) {
      return;
    }
    const held = settled.held;
    if (held != null) {
      setMe(known => (known == null ? known : withLevel(known, held)));
    }
    setWished(null);
    if (!settled.taken) {
      setError(shownError(settled.failure, texts.somethingWrong));
      // the network may have kept the level and only failed to apply it: what it holds is asked again
      backend
        .me()
        .then(setMe)
        .catch(() => undefined);
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
  // a country's flag; a globe while the exit the person keeps is not known yet; Авто's badge otherwise
  let placeBadge = <span className="code">{AUTO_BADGE}</span>;
  if (current != null) {
    placeBadge = <Flag code={current.country} />;
  } else if (pinned != null) {
    placeBadge = <Flag code={null} />;
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
  const shownLevel = wished ?? filter?.applies ?? null;
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
            {placeBadge}
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
              <ProtectionTitle onExplain={() => setExplaining(true)} />
              <span className="strong">{texts.filters[filter.applies]}</span>
            </span>
            <span className="muted small">{texts.setByAdult}</span>
          </div>
        )}
        {filter != null && !filter.locked && (
          <div className="group">
            <ProtectionTitle onExplain={() => setExplaining(true)} />
            <div className="chips" role="group" aria-label={texts.protection} aria-busy={wished != null}>
              {LEVELS.map(level => (
                <button
                  key={level}
                  type="button"
                  className="chip"
                  aria-pressed={shownLevel === level}
                  onClick={() => void protect(level)}
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
              <ProtectionTitle onExplain={() => setExplaining(true)} />
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
      {explaining && <ProtectionLevels onClose={() => setExplaining(false)} />}
    </main>
  );
}
