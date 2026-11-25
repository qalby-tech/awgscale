// «Сеть»: the owner's network, from the approved screen AppNet. A mini map of where today's lookups go (a
// branch pressed shows only its rules) with the mode beside it, then three parts: the rules in the order that
// decides (a label pressed sends the rule elsewhere, ↑↓ move it, its name opens whom it is for), the exits
// (their load and share; a share of 0 switches one off), the groups of people (a group opens its people, who
// can be paused or removed; an invitation by QR). Changes to the network are staged and sent together with
// «Применить · N»; a pause or a removal goes at once. A server without the owner's part shows what the app
// showed before: the panel does it.
import { useCallback, useEffect, useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import {
  allOff,
  Branch,
  BRANCHES,
  byExit,
  changeCount,
  groupsOf,
  inBranch,
  loads,
  modeChanged,
  moved,
  nextWay,
  NOTHING_STAGED,
  orderChanged,
  orderedRules,
  ruleChanges,
  sharesChanged,
  sharesOf,
  SHARE_STEP,
  type Staged,
  stepped,
  sumOf,
  PERCENT,
  toggledOff,
  trafficShares,
} from "../network";
import {
  ExitState,
  Filter,
  Mode,
  type Network as NetworkShown,
  type OwnerExit,
  type OwnerGroup,
  type OwnerRule,
  type Way,
} from "../types";
import { InviteSheet } from "./Invite";
import { OwnerLater } from "./OwnerLater";
import { GroupPeople } from "./People";
import { RuleGroupsSheet } from "./RuleGroups";

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
  const [staged, setStaged] = useState<Staged>(NOTHING_STAGED);
  const [applying, setApplying] = useState(false);
  const [done, setDone] = useState(false);
  const [inviting, setInviting] = useState(false);
  // the rule whose groups are being chosen
  const [scoping, setScoping] = useState<OwnerRule | null>(null);
  // the group whose people are shown; undefined: the list of groups
  const [opened, setOpened] = useState<OwnerGroup | undefined>(undefined);

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

  const changes = network === WAITING ? 0 : changeCount(network, staged);

  // Sent in this order, each part forgotten once it is applied: the rules (where, for whom, the order), the
  // mode, the shares. The first refusal stops the rest, which stays staged.
  async function apply(current: NetworkShown) {
    setApplying(true);
    setDone(false);
    setError("");
    try {
      const rules = ruleChanges(current.rules, staged);
      const order = orderChanged(current.rules, staged.order) ? staged.order : null;
      if (rules.length > 0 || order != null) {
        await backend.routeRules(rules, order, current.rules_v);
        setStaged(now => ({ ...now, ways: {}, groups: {}, order: null }));
      }
      if (staged.mode != null && modeChanged(current, staged.mode)) {
        await backend.setMode(staged.mode);
        setStaged(now => ({ ...now, mode: null }));
      }
      if (staged.shares != null && sharesChanged(current.exits, staged.shares)) {
        await backend.setShares(
          byExit(current.exits, staged.shares),
          byExit(
            current.exits,
            current.exits.map(exit => exit.weight),
          ),
        );
        setStaged(now => ({ ...now, shares: null }));
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
            mode={staged.mode ?? network.mode}
            onMode={mode => setStaged({ ...staged, mode })}
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
              network={network}
              staged={staged}
              branch={branch}
              onStage={(rule, way) => setStaged({ ...staged, ways: { ...staged.ways, [rule.id]: way } })}
              onMove={(rule, by) => setStaged({ ...staged, order: moved(network.rules, staged.order, rule.id, by) })}
              onScope={setScoping}
            />
          )}
          {part === Part.Exits && (
            <Exits
              exits={network.exits}
              shares={staged.shares ?? sharesOf(network.exits)}
              onShares={shares => setStaged({ ...staged, shares })}
            />
          )}
          {part === Part.People &&
            (opened === undefined ? (
              <People groups={network.groups} onOpen={setOpened} onInvite={() => setInviting(true)} />
            ) : (
              <GroupPeople
                group={opened}
                owners={network.owners}
                onBack={() => setOpened(undefined)}
                onChanged={load}
              />
            ))}
        </>
      )}
      {inviting && network != null && <InviteSheet groups={network.groups} onClose={() => setInviting(false)} />}
      {scoping != null && network != null && (
        <RuleGroupsSheet
          rule={scoping}
          groups={network.groups}
          chosen={groupsOf(scoping, staged)}
          onChoose={ids => {
            setStaged({ ...staged, groups: { ...staged.groups, [scoping.id]: ids } });
            setScoping(null);
          }}
          onClose={() => setScoping(null)}
        />
      )}
    </main>
  );
}

interface TrafficMapProps {
  network: NetworkShown;
  branch: Branch | null;
  // the mode as staged
  mode: Mode;
  onMode: (mode: Mode) => void;
  onBranch: (branch: Branch) => void;
}

const MODES: Mode[] = [Mode.List, Mode.Foreign];

// From the people through the entry to the three branches; a branch's width is its share of the day.
const BRANCH_PATHS: Record<Branch, string> = {
  [Branch.Exit]: "M86 56 C120 56 120 20 156 20",
  [Branch.Direct]: "M86 56 C120 56 120 56 156 56",
  [Branch.Block]: "M86 56 C120 56 120 92 156 92",
};
const THINNEST = 3;
const THICKEST = 14;

function TrafficMap({ network, branch, mode, onMode, onBranch }: TrafficMapProps) {
  const texts = useTexts();
  const parts = trafficShares(network.traffic);
  return (
    <section className="card traffic" aria-label={texts.net.whereTraffic}>
      {!network.single_server && (
        <div className="row mode-row">
          <span className="grow small muted">{texts.net.abroad}</span>
          <div className="chips" role="group" aria-label={texts.net.abroad}>
            {MODES.map(shownMode => (
              <button
                key={shownMode}
                type="button"
                className={`chip${shownMode === mode && mode !== network.mode ? " staged" : ""}`}
                aria-pressed={shownMode === mode}
                onClick={() => onMode(shownMode)}
              >
                {texts.net.modeChoices[shownMode]}
              </button>
            ))}
          </div>
        </div>
      )}
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
  network: NetworkShown;
  staged: Staged;
  branch: Branch | null;
  onStage: (rule: OwnerRule, way: Way) => void;
  onMove: (rule: OwnerRule, by: number) => void;
  onScope: (rule: OwnerRule) => void;
}

const UP = -1;
const DOWN = 1;

function Rules({ network, staged, branch, onStage, onMove, onScope }: RulesProps) {
  const texts = useTexts();
  const names = new Map(network.groups.map(group => [group.id, group.name]));
  // whom a rule is for can be chosen only when there are groups of people to choose
  const scopable = network.groups.some(group => group.id != null);
  const rules = orderedRules(network.rules, staged.order);

  function ruleMeta(rule: OwnerRule): string {
    const groups = groupsOf(rule, staged);
    if (groups.length > 0) {
      return texts.net.onlyFor(groups.map(id => names.get(id) ?? id).join(", "));
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
        const way = staged.ways[rule.id] ?? rule.route;
        if (!inBranch(way, branch)) {
          return null;
        }
        const groups = groupsOf(rule, staged);
        const scoped = groups.length > 0;
        const changed = ruleChanges([rule], staged).length > 0 || rule.id !== network.rules[order]?.id;
        const canScope = scopable && rule.movable;
        return (
          <div key={rule.id} className={`rule${changed ? " staged" : ""}`}>
            {branch == null ? (
              <span className="mover">
                <button
                  type="button"
                  className="nudge"
                  aria-label={`${texts.net.moveUp} ${rule.name}`}
                  disabled={order === 0}
                  onClick={() => onMove(rule, UP)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="nudge"
                  aria-label={`${texts.net.moveDown} ${rule.name}`}
                  disabled={order === rules.length - 1}
                  onClick={() => onMove(rule, DOWN)}
                >
                  ↓
                </button>
              </span>
            ) : (
              <span className="mono small faint order">{order + 1}</span>
            )}
            {canScope ? (
              <button
                type="button"
                className="grow rule-name"
                aria-label={`${texts.net.forWhom} ${rule.name}`}
                onClick={() => onScope(rule)}
              >
                <span className="title">{rule.name}</span>
                <span className={`small ${scoped ? "scoped" : "muted"}`}>{ruleMeta(rule)}</span>
              </button>
            ) : (
              <span className="grow">
                <span className="strong">{rule.name}</span>
                <span className={`small ${scoped ? "scoped" : "muted"}`}>{ruleMeta(rule)}</span>
              </span>
            )}
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
  const noneOn = allOff(shares);

  function step(index: number, by: number) {
    onShares(shares.map((share, position) => (position === index ? stepped(share, by) : share)));
  }

  return (
    <div className="stack tight">
      <div className="row small muted">
        <span className="grow">{texts.net.exitsHint}</span>
        {noneOn ? (
          <span className="danger-text">{texts.net.allOff}</span>
        ) : (
          sum !== PERCENT && <span className="danger-text">{texts.net.sharesSum(sum)}</span>
        )}
      </div>
      {exits.map((exit, index) => (
        <div key={exit.index} className={`exit-row ${exit.state}${shares[index] === 0 ? " off" : ""}`}>
          <span className={`dot ${exit.state}`} />
          <span className="grow">
            <span className="strong">{exit.name}</span>
            <span className={`small ${exit.state === ExitState.Down ? "danger-text" : "muted"}`}>
              {[
                exit.country?.toUpperCase(),
                texts.net.exitStates[exit.state],
                shares[index] === 0 ? texts.net.exitOff : undefined,
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
          {adjustable ? (
            <button
              type="button"
              className="mono small share toggle"
              aria-label={`${shares[index] === 0 ? texts.net.switchOn : texts.net.switchOff} ${exit.name}`}
              onClick={() => onShares(toggledOff(shares, index))}
            >
              {shares[index] === 0 ? texts.net.off : `${shares[index]}%`}
            </button>
          ) : (
            <span className="mono small share">{shares[index]}%</span>
          )}
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
  onOpen: (group: OwnerGroup) => void;
  onInvite: () => void;
}

function People({ groups, onOpen, onInvite }: PeopleProps) {
  const texts = useTexts();
  return (
    <div className="stack tight">
      {groups.map(group => {
        const name = group.id == null ? texts.net.noGroup : group.name;
        const filter = Object.values(Filter).find(known => known === group.filter);
        const level = filter == null ? undefined : texts.filters[filter];
        return (
          <button
            key={group.id ?? ""}
            type="button"
            className="person-group"
            aria-label={`${texts.net.peopleOf} ${name}`}
            onClick={() => onOpen(group)}
          >
            <span className={`initial${group.id == null ? " none" : ""}`}>
              {group.id == null ? "·" : name.slice(0, 1).toUpperCase()}
            </span>
            <span className="grow">
              <span className="title">{name}</span>
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
            <span className="muted">›</span>
          </button>
        );
      })}
      <button type="button" className="primary" onClick={onInvite}>
        {texts.net.invite}
      </button>
    </div>
  );
}
