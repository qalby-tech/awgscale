// The owner's network screen without its drawing: where a rule goes next, what is staged, the shares of the
// exits in percent and the day's lookups as parts of a whole. Pure functions, tested in network.test.ts.
import { type Mode, type Network, type OwnerExit, type OwnerRule, type RuleChange, type Traffic, Way } from "./types";

export const PERCENT = 100;
// one press of − or + moves an exit's share by this much
export const SHARE_STEP = 5;
// a share of 0 switches the exit off
export const MIN_SHARE = 0;
export const MAX_SHARE = 100;

// What the owner has staged and «Применить» sends: where rules go, whom they are for, their order, the mode and
// the shares of the exits. Nothing staged is the network as the server said it.
export interface Staged {
  ways: Record<string, Way>;
  // the ids of the groups a rule is for; empty: everybody
  groups: Record<string, string[]>;
  // every rule's id in the new order; null: as it is
  order: string[] | null;
  mode: Mode | null;
  shares: number[] | null;
}

export const NOTHING_STAGED: Staged = { ways: {}, groups: {}, order: null, mode: null, shares: null };

// Where a rule that may go anywhere goes next: abroad → directly → blocked → off → abroad
const NEXT_WAY: Record<Way, Way> = {
  [Way.Exit]: Way.Direct,
  [Way.Direct]: Way.Block,
  [Way.Block]: Way.Off,
  [Way.Off]: Way.Exit,
};

// The next place of a rule when its label is pressed. A list by link, the group whose networks update by
// themselves and a group with networks only go their own way or off.
export function nextWay(rule: OwnerRule, now: Way): Way {
  if (rule.movable) {
    return NEXT_WAY[now];
  }
  return now === Way.Off ? rule.way : Way.Off;
}

function sameGroups(first: string[], second: string[]): boolean {
  return first.length === second.length && [...first].sort().join(" ") === [...second].sort().join(" ");
}

// The rules staged elsewhere than they are, or for other groups: what «Применить» sends.
export function ruleChanges(rules: OwnerRule[], staged: Staged): RuleChange[] {
  const changes: RuleChange[] = [];
  for (const rule of rules) {
    const way = staged.ways[rule.id];
    const groups = staged.groups[rule.id];
    const change: RuleChange = { rule: rule.id };
    if (way !== undefined && way !== rule.route) {
      change.route = way;
    }
    if (groups !== undefined && !sameGroups(groups, rule.group_ids)) {
      change.groups = [...groups].sort();
    }
    if (change.route !== undefined || change.groups !== undefined) {
      changes.push(change);
    }
  }
  return changes;
}

// The rules in the order shown: the staged one, else as the server said.
export function orderedRules(rules: OwnerRule[], order: string[] | null): OwnerRule[] {
  if (order == null) {
    return rules;
  }
  const byId = new Map(rules.map(rule => [rule.id, rule]));
  const known = order.map(id => byId.get(id)).filter((rule): rule is OwnerRule => rule !== undefined);
  return known.length === rules.length ? known : rules;
}

// A rule moved up (-1) or down (+1) by one place; at the edge nothing moves.
export function moved(rules: OwnerRule[], order: string[] | null, id: string, by: number): string[] {
  const ids = orderedRules(rules, order).map(rule => rule.id);
  const from = ids.indexOf(id);
  const to = from + by;
  if (from < 0 || to < 0 || to >= ids.length) {
    return ids;
  }
  [ids[from], ids[to]] = [ids[to], ids[from]];
  return ids;
}

// The staged order differs from the server's.
export function orderChanged(rules: OwnerRule[], order: string[] | null): boolean {
  return order != null && orderedRules(rules, order).some((rule, index) => rule.id !== rules[index].id);
}

// The groups a rule is for, as staged.
export function groupsOf(rule: OwnerRule, staged: Staged): string[] {
  return staged.groups[rule.id] ?? rule.group_ids;
}

// The shares of the exits in percent: whole numbers that add up to 100, the largest takes what rounding
// leaves (the same sum the server and the panel make).
export function sharesOf(exits: OwnerExit[]): number[] {
  const weights = exits.map(exit => exit.weight);
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  if (total === 0) {
    return weights.map(() => 0);
  }
  const percents = weights.map(weight => Math.round((PERCENT * weight) / total));
  const largest = weights.indexOf(Math.max(...weights));
  percents[largest] += PERCENT - percents.reduce((sum, share) => sum + share, 0);
  return percents;
}

// An exit's share moved by `step`, within what the server takes; 0 is the exit switched off.
export function stepped(share: number, step: number): number {
  return Math.min(MAX_SHARE, Math.max(MIN_SHARE, share + step));
}

export function sumOf(shares: number[]): number {
  return shares.reduce((sum, share) => sum + share, 0);
}

// The staged shares differ from the stored ones.
export function sharesChanged(exits: OwnerExit[], staged: number[] | null): boolean {
  if (staged == null) {
    return false;
  }
  const stored = sharesOf(exits);
  return staged.some((share, index) => share !== stored[index]);
}

// {exit number: value}: what the server takes for the shares and their base.
export function byExit(exits: OwnerExit[], values: number[]): Record<number, number> {
  return Object.fromEntries(exits.map((exit, index) => [exit.index, values[index]]));
}

// An exit switched off or back on with one press, the sum kept at 100: off, its share goes to the exit with the
// largest share; back on, it takes one step from that exit. Nothing changes when no other exit could give or take.
export function toggledOff(shares: number[], index: number): number[] {
  const others = shares.map((share, position) => (position === index ? -1 : share));
  const largest = others.indexOf(Math.max(...others));
  if (largest < 0 || others[largest] <= 0) {
    return shares;
  }
  const next = [...shares];
  if (shares[index] > 0) {
    next[largest] += shares[index];
    next[index] = 0;
    return next;
  }
  const taken = Math.min(SHARE_STEP, shares[largest]);
  next[largest] -= taken;
  next[index] = taken;
  return next;
}

// Every exit would be switched off: the server refuses that.
export function allOff(shares: number[]): boolean {
  return shares.length > 0 && shares.every(share => share === 0);
}

// The mode staged differs from the server's.
export function modeChanged(network: Network, mode: Mode | null): boolean {
  return mode != null && mode !== network.mode;
}

// How many changes «Применить · N» counts: each rule sent elsewhere or made for other groups, the order, the
// mode and the shares as one each.
export function changeCount(network: Network, staged: Staged): number {
  return (
    ruleChanges(network.rules, staged).length +
    (orderChanged(network.rules, staged.order) ? 1 : 0) +
    (modeChanged(network, staged.mode) ? 1 : 0) +
    (sharesChanged(network.exits, staged.shares) ? 1 : 0)
  );
}

export enum Branch {
  Exit = "exit",
  Direct = "direct",
  Block = "block",
}

export const BRANCHES: Branch[] = [Branch.Exit, Branch.Direct, Branch.Block];

// The day's lookups as whole percents of all of them; all zero when there were none.
export function trafficShares(traffic: Traffic): Record<Branch, number> {
  const total = traffic.exit + traffic.direct + traffic.block;
  const part = (value: number) => (total === 0 ? 0 : Math.round((PERCENT * value) / total));
  return {
    [Branch.Exit]: part(traffic.exit),
    [Branch.Direct]: part(traffic.direct),
    [Branch.Block]: part(traffic.block),
  };
}

// Does a rule (where it goes as staged) belong to the branch the person picked on the mini map?
export function inBranch(way: Way, branch: Branch | null): boolean {
  if (branch == null) {
    return true;
  }
  const ways: Record<Branch, Way> = { [Branch.Exit]: Way.Exit, [Branch.Direct]: Way.Direct, [Branch.Block]: Way.Block };
  return way === ways[branch];
}

// The busiest exit carries the full bar; the others in proportion (users on them now).
export function loads(exits: OwnerExit[]): number[] {
  const most = Math.max(0, ...exits.map(exit => exit.users));
  return exits.map(exit => (most === 0 ? 0 : exit.users / most));
}
