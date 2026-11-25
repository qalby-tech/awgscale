// «Сеть»: the owner's network, from the approved screen AppNet. A mini map of where today's lookups go (a
// branch pressed shows only its rules), then three parts: the rules in the order that decides (a label
// pressed sends the rule elsewhere), the exits (their load and share), the groups of people (and an invitation
// by QR). Changes are staged and sent together with «Применить · N». A server without the owner's part shows
// what the app showed before: the panel does it.
import { useCallback, useEffect, useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import {
  Branch,
  BRANCHES,
  byExit,
  changeCount,
  inBranch,
  loads,
  nextWay,
  ruleChanges,
  sharesChanged,
  sharesOf,
  SHARE_STEP,
  stepped,
  sumOf,
  PERCENT,
  trafficShares,
} from "../network";
import {
  ExitState,
  Filter,
  type Network as NetworkShown,
  type OwnerExit,
  type OwnerGroup,
  type OwnerRule,
  type Way,
} from "../types";
import { InviteSheet } from "./Invite";
import { OwnerLater } from "./OwnerLater";

enum Part {
  Rules = "rules",
  Exits = "exits",
  People = "people",
}

const PARTS: Part[] = [Part.Rules, Part.Exits, Part.People];

// The answer is being waited for; null is a server without the owner's part.
const WAITING = undefined;

interface NetworkProps {
  host: string;
  // the tab is in front: the network is asked again each time it comes there
  shown: boolean;
}

export function Network({ host, shown }: NetworkProps) {
  const texts = useTexts();
  const [network, setNetwork] = useState<NetworkShown | null | typeof WAITING>(WAITING);
  const [error, setError] = useState("");
  const [part, setPart] = useState(Part.Rules);
  const [branch, setBranch] = useState<Branch | null>(null);
  const [staged, setStaged] = useState<Record<string, Way>>({});
  const [shares, setShares] = useState<number[] | null>(null);
  const [applying, setApplying] = useState(false);
  const [done, setDone] = useState(false);
  const [inviting, setInviting] = useState(false);

  const load = useCallback(() => {
    backend
      .network()
      .then(answer => {
        setNetwork(answer);
        setError("");
      })
      .catch((failure: unknown) => setError(shownError(failure, texts.somethingWrong)));
  }, [texts]);

  useEffect(() => {
    if (shown) {
      load();
    }
  }, [shown, load]);

  if (network === null) {
    return <OwnerLater host={host} what={texts.tabs.network} />;
  }

  const changes = network === WAITING ? 0 : changeCount(network, staged, shares);

  async function apply(current: NetworkShown) {
    setApplying(true);
    setDone(false);
    setError("");
    try {
      const rules = ruleChanges(current.rules, staged);
      if (rules.length > 0) {
        await backend.routeRules(rules, current.rules_v);
        setStaged({});
      }
      if (shares != null && sharesChanged(current.exits, shares)) {
        await backend.setShares(
          byExit(current.exits, shares),
          byExit(
            current.exits,
            current.exits.map(exit => exit.weight),
          ),
        );
        setShares(null);
      }
      setDone(true);
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    } finally {
      setApplying(false);
      load();
    }
  }

  const down = network === WAITING ? 0 : network.exits.filter(exit => exit.state === ExitState.Down).length;

  return (
    <main className="screen">
      <header className="head">
        <span>
          <h1>{texts.tabs.network}</h1>
          {network !== WAITING && (
            <span className="muted small">{texts.net.summary(network.users, network.exits.length, down)}</span>
          )}
        </span>
        {network !== WAITING && changes > 0 && (
          <button type="button" className="apply" disabled={applying} onClick={() => void apply(network)}>
            {applying ? texts.net.applying : texts.net.apply(changes)}
          </button>
        )}
      </header>
      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {done && changes === 0 && (
        <p className="note small" role="status">
          {texts.net.applied}
        </p>
      )}
      {network === WAITING ? (
        <section className="card" aria-busy="true" />
      ) : (
        <>
          {network.stale && <p className="note small">{texts.net.stale}</p>}
          <TrafficMap
            network={network}
            branch={branch}
            onBranch={picked => {
              setBranch(branch === picked ? null : picked);
              setPart(Part.Rules);
            }}
          />
          <div className="segments" role="tablist">
            {PARTS.map(shownPart => (
              <button
                key={shownPart}
                type="button"
                role="tab"
                aria-selected={shownPart === part}
                onClick={() => setPart(shownPart)}
              >
                {texts.net.parts[shownPart]}
              </button>
            ))}
          </div>
          {part === Part.Rules && (
            <Rules
              rules={network.rules}
              staged={staged}
              branch={branch}
              onStage={(rule, way) => setStaged({ ...staged, [rule.id]: way })}
            />
          )}
          {part === Part.Exits && (
            <Exits exits={network.exits} shares={shares ?? sharesOf(network.exits)} onShares={setShares} />
          )}
          {part === Part.People && <People groups={network.groups} onInvite={() => setInviting(true)} />}
        </>
      )}
      {inviting && network != null && <InviteSheet groups={network.groups} onClose={() => setInviting(false)} />}
    </main>
  );
}

interface TrafficMapProps {
  network: NetworkShown;
  branch: Branch | null;
  onBranch: (branch: Branch) => void;
}

// From the people through the entry to the three branches; a branch's width is its share of the day.
const BRANCH_PATHS: Record<Branch, string> = {
  [Branch.Exit]: "M86 56 C120 56 120 20 156 20",
  [Branch.Direct]: "M86 56 C120 56 120 56 156 56",
  [Branch.Block]: "M86 56 C120 56 120 92 156 92",
};
const THINNEST = 3;
const THICKEST = 14;

function TrafficMap({ network, branch, onBranch }: TrafficMapProps) {
  const texts = useTexts();
  const parts = trafficShares(network.traffic);
  return (
    <section className="card traffic" aria-label={texts.net.whereTraffic}>
      <div className="row small muted">
        <span className="grow">{texts.net.whereTraffic}</span>
        <span>{branch == null ? texts.net.pickBranch : texts.net.onlyBranch(texts.net.branches[branch])}</span>
      </div>
      <div className="map">
        <svg width="160" height="112" viewBox="0 0 160 112" aria-hidden="true">
          {BRANCHES.map(shownBranch => (
            <path
              key={shownBranch}
              d={BRANCH_PATHS[shownBranch]}
              className={`branch ${shownBranch}`}
              strokeWidth={THINNEST + ((THICKEST - THINNEST) * parts[shownBranch]) / PERCENT}
              opacity={branch == null || branch === shownBranch ? 0.9 : 0.15}
            />
          ))}
          <path d="M40 56 H70" className="trunk" />
          <rect x="0" y="38" width="62" height="36" rx="10" className="people" />
          <text x="31" y="61" textAnchor="middle" className="people-label">
            {texts.net.people(network.users)}
          </text>
          <circle cx="80" cy="56" r="13" className="entry" />
          <text x="80" y="60" textAnchor="middle" className="entry-label">
            in
          </text>
        </svg>
        <div className="outs">
          {BRANCHES.map(shownBranch => (
            <button
              key={shownBranch}
              type="button"
              className={`out ${shownBranch}`}
              aria-label={texts.net.branches[shownBranch]}
              aria-pressed={branch === shownBranch}
              onClick={() => onBranch(shownBranch)}
            >
              <span className="grow">{texts.net.branches[shownBranch]}</span>
              <span className="mono small">{parts[shownBranch]}%</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

interface RulesProps {
  rules: OwnerRule[];
  staged: Record<string, Way>;
  branch: Branch | null;
  onStage: (rule: OwnerRule, way: Way) => void;
}

function Rules({ rules, staged, branch, onStage }: RulesProps) {
  const texts = useTexts();

  function ruleMeta(rule: OwnerRule): string {
    if (rule.groups.length > 0) {
      return texts.net.onlyFor(rule.groups.join(", "));
    }
    return rule.by_link ? texts.net.byLink : texts.net.ruleSites(rule.items);
  }

  if (rules.length === 0) {
    return <p className="note">{texts.net.noRules}</p>;
  }
  return (
    <div className="stack tight">
      <div className="row small muted">
        <span className="grow">{texts.net.rulesHint}</span>
        <span>{texts.net.rulesTap}</span>
      </div>
      {rules.map((rule, order) => {
        const way = staged[rule.id] ?? rule.route;
        if (!inBranch(way, branch)) {
          return null;
        }
        const scoped = rule.groups.length > 0;
        return (
          <div key={rule.id} className={`rule${way === rule.route ? "" : " staged"}`}>
            <span className="mono small faint order">{order + 1}</span>
            <span className="grow">
              <span className="strong">{rule.name}</span>
              <span className={`small ${scoped ? "scoped" : "muted"}`}>{ruleMeta(rule)}</span>
            </span>
            <button
              type="button"
              className={`way ${way}`}
              aria-label={`${rule.name}: ${texts.net.ways[way]}`}
              onClick={() => onStage(rule, nextWay(rule, way))}
            >
              {texts.net.ways[way]}
            </button>
          </div>
        );
      })}
    </div>
  );
}

interface ExitsProps {
  exits: OwnerExit[];
  shares: number[];
  onShares: (shares: number[]) => void;
}

function Exits({ exits, shares, onShares }: ExitsProps) {
  const texts = useTexts();
  if (exits.length === 0) {
    return <p className="note">{texts.net.noExits}</p>;
  }
  const bars = loads(exits);
  const sum = sumOf(shares);
  // one exit carries everybody: its share is not changed
  const adjustable = exits.length > 1;

  function step(index: number, by: number) {
    onShares(shares.map((share, position) => (position === index ? stepped(share, by) : share)));
  }

  return (
    <div className="stack tight">
      <div className="row small muted">
        <span className="grow">{texts.net.exitsHint}</span>
        {sum !== PERCENT && <span className="danger-text">{texts.net.sharesSum(sum)}</span>}
      </div>
      {exits.map((exit, index) => (
        <div key={exit.index} className={`exit-row ${exit.state}`}>
          <span className={`dot ${exit.state}`} />
          <span className="grow">
            <span className="strong">{exit.name}</span>
            <span className={`small ${exit.state === ExitState.Down ? "danger-text" : "muted"}`}>
              {[
                exit.country?.toUpperCase(),
                texts.net.exitStates[exit.state],
                exit.state === ExitState.Down ? undefined : texts.net.peopleOn(exit.users, exit.online),
              ]
                .filter(part => part != null && part.length > 0)
                .join(" · ")}
            </span>
          </span>
          <span className="bar" aria-hidden="true">
            <span style={{ width: `${Math.round(bars[index] * PERCENT)}%` }} />
          </span>
          {adjustable && (
            <button
              type="button"
              className="step"
              aria-label={`${texts.net.lessShare} ${exit.name}`}
              onClick={() => step(index, -SHARE_STEP)}
            >
              −
            </button>
          )}
          <span className="mono small share">{shares[index]}%</span>
          {adjustable && (
            <button
              type="button"
              className="step"
              aria-label={`${texts.net.moreShare} ${exit.name}`}
              onClick={() => step(index, SHARE_STEP)}
            >
              +
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

interface PeopleProps {
  groups: OwnerGroup[];
  onInvite: () => void;
}

function People({ groups, onInvite }: PeopleProps) {
  const texts = useTexts();
  return (
    <div className="stack tight">
      {groups.map(group => {
        const name = group.id == null ? texts.net.noGroup : group.name;
        const filter = Object.values(Filter).find(known => known === group.filter);
        const level = filter == null ? undefined : texts.filters[filter];
        return (
          <div key={group.id ?? ""} className="person-group">
            <span className={`initial${group.id == null ? " none" : ""}`}>
              {group.id == null ? "·" : name.slice(0, 1).toUpperCase()}
            </span>
            <span className="grow">
              <span className="strong">{name}</span>
              <span className="small muted">
                {[texts.net.peopleOn(group.users, group.online), level, group.space ? texts.net.space : undefined]
                  .filter(part => part != null)
                  .join(" · ")}
              </span>
            </span>
            <span className="online small">
              <span className="dot up" />
              {group.online}
            </span>
          </div>
        );
      })}
      <button type="button" className="primary" onClick={onInvite}>
        {texts.net.invite}
      </button>
    </div>
  );
}
