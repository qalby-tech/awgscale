// Локация: Авто, the exits chosen lately, the countries, and a country's servers by load. A person sees
// the servers by their address, never by the exit's name. With a thousand exits nothing renders all of
// them: the countries, ten servers of one, and the first search results.
import { useMemo, useState } from "react";

import { useTexts } from "../i18n";
import {
  addressesMatching,
  badgeOf,
  type Country,
  countriesMatching,
  countriesOf,
  isAddressQuery,
  RESULTS_SHOWN,
  SERVERS_SHOWN,
  shownAddress,
} from "../locations";
import { DestinationKind, type Destination, type Exit } from "../types";
import { LockIcon, SearchIcon } from "./icons";

// Says whether the choice was taken: the list then goes back to the connection
type Choose = (destination: Destination) => Promise<boolean>;

interface PlacesProps {
  exits: Exit[];
  // the exit the person is kept on now, and whether they chose it themselves
  current: Exit | null;
  pinned: number | null;
  recent: Exit[];
  busy: boolean;
  error: string;
  onChoose: Choose;
  onClose: () => void;
}

interface BackProps {
  title: string;
  onBack: () => void;
}

function Back({ title, onBack }: BackProps) {
  const texts = useTexts();
  return (
    <header className="head">
      <button type="button" className="close" aria-label={texts.back} onClick={onBack}>
        ‹
      </button>
      <h1 className="grow">{title}</h1>
    </header>
  );
}

interface SearchProps {
  label: string;
  placeholder: string;
  value: string;
  numeric: boolean;
  onChange: (value: string) => void;
}

function Search({ label, placeholder, value, numeric, onChange }: SearchProps) {
  return (
    <label className="search">
      <SearchIcon />
      <input
        type="search"
        aria-label={label}
        placeholder={placeholder}
        inputMode={numeric ? "decimal" : undefined}
        value={value}
        onChange={event => onChange(event.target.value)}
      />
    </label>
  );
}

function More({ shown, total }: { shown: number; total: number }) {
  const texts = useTexts();
  return total > shown ? <p className="note small">{texts.shownOf(shown, total)}</p> : null;
}

interface ServerProps {
  exit: Exit;
  meta: string;
  pressed: boolean;
  locked: boolean;
  disabled: boolean;
  onClick: () => void;
}

// One server in a list: its country's code, its address, and what is said of it
function Server({ exit, meta, pressed, locked, disabled, onClick }: ServerProps) {
  return (
    <button type="button" className="option" aria-pressed={pressed} disabled={disabled} onClick={onClick}>
      <span className="code">{badgeOf(exit.country)}</span>
      <span className="grow">
        <span className="strong mono">{shownAddress(exit)}</span>
        {meta.length > 0 && <span className="muted small">{meta}</span>}
      </span>
      {locked && (
        <span className="muted">
          <LockIcon />
        </span>
      )}
    </button>
  );
}

interface CountryRowProps {
  country: Country;
  onOpen: () => void;
}

function CountryRow({ country, onOpen }: CountryRowProps) {
  const texts = useTexts();
  const count = country.working.length;
  return (
    <button type="button" className="option" disabled={count === 0} onClick={onOpen}>
      <span className="code">{badgeOf(country.code)}</span>
      <span className="grow strong">{country.name}</span>
      <span className="count" aria-label={texts.workingServers(count)}>
        {count}
      </span>
      <span className="muted">›</span>
    </button>
  );
}

export function Places({ exits, current, pinned, recent, busy, error, onChoose, onClose }: PlacesProps) {
  const texts = useTexts();
  const [query, setQuery] = useState("");
  // the code of the country whose servers are shown (null: «Другие»); undefined while none is open
  const [opened, setOpened] = useState<string | null | undefined>(undefined);
  const countries = useMemo(
    () => countriesOf(exits, texts.locale, texts.otherCountries),
    [exits, texts.locale, texts.otherCountries],
  );

  const country = opened === undefined ? undefined : countries.find(candidate => candidate.code === opened);
  if (country != null) {
    return (
      <CountryServers
        country={country}
        exits={exits}
        pinned={pinned}
        busy={busy}
        error={error}
        onChoose={onChoose}
        onBack={() => setOpened(undefined)}
      />
    );
  }

  function nameOf(exit: Exit): string {
    return countries.find(candidate => candidate.code === exit.country)?.name ?? texts.otherCountries;
  }

  async function take(destination: Destination) {
    if (await onChoose(destination)) {
      onClose();
    }
  }

  function server(exit: Exit, meta: string, locked: boolean) {
    return (
      <Server
        key={exit.index}
        exit={exit}
        meta={meta}
        pressed={pinned === exit.index}
        locked={locked}
        disabled={busy || !exit.up}
        onClick={() => void take({ kind: DestinationKind.Exit, index: exit.index })}
      />
    );
  }

  function found() {
    const wanted = query.trim();
    if (isAddressQuery(wanted)) {
      const matching = addressesMatching(exits, wanted);
      return (
        <section className="stack tight">
          {matching.slice(0, RESULTS_SHOWN).map(exit => server(exit, nameOf(exit), pinned === exit.index))}
          {matching.length === 0 && <p className="note">{texts.nothingFound}</p>}
          <More shown={RESULTS_SHOWN} total={matching.length} />
        </section>
      );
    }
    const matching = countriesMatching(countries, wanted, texts.locale);
    return (
      <section className="stack tight">
        {matching.slice(0, RESULTS_SHOWN).map(shown => (
          <CountryRow key={shown.code ?? ""} country={shown} onOpen={() => setOpened(shown.code)} />
        ))}
        {matching.length === 0 && <p className="note">{texts.nothingFound}</p>}
        <More shown={RESULTS_SHOWN} total={matching.length} />
      </section>
    );
  }

  function currentMeta(exit: Exit): string {
    const where = nameOf(exit);
    if (pinned === exit.index) {
      return `${where} · ${texts.pinned}`;
    }
    return exit.up ? where : `${where} · ${texts.exitDown}`;
  }

  return (
    <main className="screen">
      <Back title={texts.location} onBack={onClose} />
      <Search
        label={texts.search}
        placeholder={texts.searchPlaceholder}
        value={query}
        numeric={false}
        onChange={setQuery}
      />
      {query.trim().length > 0 ? (
        found()
      ) : (
        <>
          <button
            type="button"
            className="option"
            aria-pressed={pinned == null}
            disabled={busy}
            onClick={() => void take({ kind: DestinationKind.Auto })}
          >
            <span className="code">A</span>
            <span className="grow strong">{texts.auto}</span>
          </button>
          {(current != null || recent.length > 0) && (
            <section className="stack tight">
              <h2 className="section">{texts.recent}</h2>
              {current != null && server(current, currentMeta(current), pinned === current.index)}
              {recent.map(exit => server(exit, nameOf(exit), false))}
            </section>
          )}
          <section className="stack tight">
            <h2 className="section">{texts.countries}</h2>
            {countries.map(shown => (
              <CountryRow key={shown.code ?? ""} country={shown} onOpen={() => setOpened(shown.code)} />
            ))}
          </section>
        </>
      )}
      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}

interface CountryServersProps {
  country: Country;
  exits: Exit[];
  pinned: number | null;
  busy: boolean;
  error: string;
  onChoose: Choose;
  onBack: () => void;
}

// One country: its best server, keeping the shown address, and its least loaded servers by address
function CountryServers({ country, exits, pinned, busy, error, onChoose, onBack }: CountryServersProps) {
  const texts = useTexts();
  const [query, setQuery] = useState("");
  const best = country.working.at(0) ?? null;
  const pinnedExit = exits.find(exit => exit.index === pinned) ?? null;
  // kept on an address of this country (it may be down for now: the network moves the person meanwhile)
  const keptHere = pinnedExit != null && pinnedExit.country === country.code;
  const bestChosen = keptHere && best != null && pinnedExit.index === best.index;
  const wanted = query.trim();
  const servers = country.working.filter(exit => shownAddress(exit).startsWith(wanted));

  function pin(exit: Exit) {
    void onChoose({ kind: DestinationKind.Exit, index: exit.index });
  }

  function keep(on: boolean) {
    if (!on) {
      void onChoose({ kind: DestinationKind.Auto });
    } else if (best != null) {
      pin(best);
    }
  }

  return (
    <main className="screen">
      <Back title={country.name} onBack={onBack} />
      {best == null && <p className="note">{texts.noWorking}</p>}
      <button
        type="button"
        className="option"
        role="radio"
        aria-checked={bestChosen}
        disabled={busy || best == null}
        onClick={() => best != null && pin(best)}
      >
        <span className="grow strong">{texts.bestServer}</span>
        <span className="radio" />
      </button>
      <button
        type="button"
        className="option"
        role="switch"
        aria-checked={keptHere}
        disabled={busy || (!keptHere && best == null)}
        onClick={() => keep(!keptHere)}
      >
        <span className="grow">
          <span className="strong">{texts.pinAddress}</span>
          <span className="muted small mono">
            {keptHere && pinnedExit != null ? shownAddress(pinnedExit) : texts.mayChange}
          </span>
        </span>
        <span className={keptHere ? "track on" : "track"}>
          <span className="knob" />
        </span>
      </button>
      {country.working.length > 0 && (
        <section className="stack tight">
          <h2 className="section">{texts.leastLoaded}</h2>
          <Search
            label={texts.searchAddress}
            placeholder={texts.searchAddress}
            value={query}
            numeric={true}
            onChange={setQuery}
          />
          <div className="stack tight" role="radiogroup" aria-label={texts.server}>
            {servers.slice(0, SERVERS_SHOWN).map(exit => (
              <button
                key={exit.index}
                type="button"
                className="option"
                role="radio"
                aria-checked={!bestChosen && pinned === exit.index}
                disabled={busy}
                onClick={() => pin(exit)}
              >
                <span className="grow strong mono">{shownAddress(exit)}</span>
                <span className="radio" />
              </button>
            ))}
            {servers.length === 0 && <p className="note">{texts.nothingFound}</p>}
          </div>
          {wanted.length > 0 && <More shown={SERVERS_SHOWN} total={servers.length} />}
        </section>
      )}
      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}
