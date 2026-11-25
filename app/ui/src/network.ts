// The owner's network screen without its drawing: where a rule goes next, what is staged, the shares of the
// exits in percent and the day's lookups as parts of a whole. Pure functions, tested in network.test.ts.
import { type Network, type OwnerExit, type OwnerRule, type RuleChange, type Traffic, Way } from "./types";

export const PERCENT = 100;
// one press of − or + moves an exit's share by this much
export const SHARE_STEP = 5;
export const MIN_SHARE = 1;
export const MAX_SHARE = 99;

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

// The rules staged elsewhere than they are: what «Применить» sends.
export function ruleChanges(rules: OwnerRule[], staged: Record<string, Way>): RuleChange[] {
  return rules
    .filter(rule => staged[rule.id] !== undefined && staged[rule.id] !== rule.route)
    .map(rule => ({ rule: rule.id, route: staged[rule.id] }));
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

// An exit's share moved by `step`, within what the server takes.
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

// How many changes «Применить · N» counts: each rule sent elsewhere, and the shares as one.
export function changeCount(network: Network, staged: Record<string, Way>, shares: number[] | null): number {
  return ruleChanges(network.rules, staged).length + (sharesChanged(network.exits, shares) ? 1 : 0);
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
