import { describe, expect, it } from "vitest";

import {
  Branch,
  byExit,
  changeCount,
  inBranch,
  loads,
  nextWay,
  ruleChanges,
  sharesChanged,
  sharesOf,
  stepped,
  trafficShares,
} from "./network";
import { ExitState, Mode, type Network, type OwnerExit, type OwnerRule, Way } from "./types";

function rule(id: string, route: Way, movable = true, way: Way = route === Way.Off ? Way.Block : route): OwnerRule {
  return { id, name: id, route, way, items: 3, by_link: !movable, movable, groups: [] };
}

function exit(index: number, weight: number, users = 0): OwnerExit {
  return { index, name: `exit${index}`, state: ExitState.Up, country: "nl", weight, share: 0, users, online: 0 };
}

function network(rules: OwnerRule[], exits: OwnerExit[]): Network {
  return {
    mode: Mode.List,
    single_server: false,
    users: 0,
    online: 0,
    traffic: { exit: 0, direct: 0, block: 0 },
    exits,
    rules,
    rules_v: "0123456789ab",
    groups: [],
    stale: false,
  };
}

describe("where a rule goes next", () => {
  it("a rule of sites goes round: abroad, directly, blocked, off", () => {
    const shown = rule("a", Way.Exit);
    expect([Way.Exit, Way.Direct, Way.Block, Way.Off].map(now => nextWay(shown, now))).toEqual([
      Way.Direct,
      Way.Block,
      Way.Off,
      Way.Exit,
    ]);
  });

  it("a list by link only goes its own way or off", () => {
    const list = rule("b", Way.Off, false, Way.Block);
    expect(nextWay(list, Way.Off)).toBe(Way.Block);
    expect(nextWay(list, Way.Block)).toBe(Way.Off);
  });
});

describe("what is staged", () => {
  it("only rules staged elsewhere than they are are sent", () => {
    const rules = [rule("first", Way.Exit), rule("second", Way.Block)];
    expect(ruleChanges(rules, { first: Way.Direct, second: Way.Block })).toEqual([
      { rule: "first", route: Way.Direct },
    ]);
  });

  it("the shares count as one change", () => {
    const shown = network([rule("first", Way.Exit)], [exit(1, 1), exit(2, 1)]);
    expect(changeCount(shown, {}, null)).toBe(0);
    expect(changeCount(shown, { first: Way.Block }, [60, 40])).toBe(2);
    expect(changeCount(shown, {}, [50, 50])).toBe(0);
  });
});

describe("the shares of the exits", () => {
  it("add up to 100 as the server makes them", () => {
    expect(sharesOf([exit(1, 1), exit(2, 1), exit(3, 1)])).toEqual([34, 33, 33]);
    expect(sharesOf([exit(1, 0)])).toEqual([0]);
  });

  it("move by steps within what the server takes", () => {
    expect(stepped(97, 5)).toBe(99);
    expect(stepped(3, -5)).toBe(1);
  });

  it("are sent by the exit's number", () => {
    const exits = [exit(1, 3), exit(4, 1)];
    expect(sharesChanged(exits, [75, 25])).toBe(false);
    expect(sharesChanged(exits, [70, 30])).toBe(true);
    expect(byExit(exits, [70, 30])).toEqual({ 1: 70, 4: 30 });
  });

  it("the busiest exit has the full bar", () => {
    expect(loads([exit(1, 1, 10), exit(2, 1, 5), exit(3, 1, 0)])).toEqual([1, 0.5, 0]);
    expect(loads([exit(1, 1, 0)])).toEqual([0]);
  });
});

describe("the mini map", () => {
  it("shows the day's lookups as percents", () => {
    expect(trafficShares({ exit: 62, direct: 31, block: 7 })).toEqual({ exit: 62, direct: 31, block: 7 });
    expect(trafficShares({ exit: 0, direct: 0, block: 0 })).toEqual({ exit: 0, direct: 0, block: 0 });
  });

  it("a branch picked shows only the rules that go there", () => {
    expect(inBranch(Way.Direct, Branch.Direct)).toBe(true);
    expect(inBranch(Way.Off, Branch.Block)).toBe(false);
    expect(inBranch(Way.Off, null)).toBe(true);
  });
});
