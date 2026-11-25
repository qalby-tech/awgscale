import { describe, expect, it } from "vitest";

import {
  allOff,
  Branch,
  byExit,
  changeCount,
  groupsOf,
  inBranch,
  loads,
  moved,
  nextWay,
  NOTHING_STAGED,
  orderChanged,
  orderedRules,
  ruleChanges,
  sharesChanged,
  sharesOf,
  type Staged,
  stepped,
  toggledOff,
  trafficShares,
} from "./network";
import { ExitState, Mode, type Network, type OwnerExit, type OwnerRule, Way } from "./types";

function rule(id: string, route: Way, movable = true, way: Way = route === Way.Off ? Way.Block : route): OwnerRule {
  return { id, name: id, route, way, items: 3, by_link: !movable, movable, groups: [], group_ids: [] };
}

function exit(index: number, weight: number, users = 0): OwnerExit {
  return {
    index,
    name: `exit${index}`,
    state: ExitState.Up,
    country: "nl",
    weight,
    share: 0,
    users,
    online: 0,
    off: weight === 0,
  };
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
    invite_base: "",
    me: "phone",
    owners: ["phone"],
  };
}

function staged(more: Partial<Staged>): Staged {
  return { ...NOTHING_STAGED, ...more };
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
    expect(ruleChanges(rules, staged({ ways: { first: Way.Direct, second: Way.Block } }))).toEqual([
      { rule: "first", route: Way.Direct },
    ]);
  });

  it("a rule made for other groups says only that; the same groups in another order are no change", () => {
    const scoped = { ...rule("first", Way.Exit), group_ids: ["kids01", "home01"] };
    expect(ruleChanges([scoped], staged({ groups: { first: ["home01", "kids01"] } }))).toEqual([]);
    expect(ruleChanges([scoped], staged({ groups: { first: [] } }))).toEqual([{ rule: "first", groups: [] }]);
    expect(ruleChanges([scoped], staged({ ways: { first: Way.Direct }, groups: { first: ["kids01"] } }))).toEqual([
      { rule: "first", route: Way.Direct, groups: ["kids01"] },
    ]);
    expect(groupsOf(scoped, NOTHING_STAGED)).toEqual(["kids01", "home01"]);
  });

  it("the shares, the order and the mode count as one change each", () => {
    const shown = network([rule("first", Way.Exit), rule("second", Way.Exit)], [exit(1, 1), exit(2, 1)]);
    expect(changeCount(shown, NOTHING_STAGED)).toBe(0);
    expect(changeCount(shown, staged({ ways: { first: Way.Block }, shares: [60, 40] }))).toBe(2);
    expect(changeCount(shown, staged({ shares: [50, 50] }))).toBe(0);
    expect(changeCount(shown, staged({ order: ["second", "first"], mode: Mode.Foreign }))).toBe(2);
    expect(changeCount(shown, staged({ order: ["first", "second"], mode: Mode.List }))).toBe(0);
  });
});

describe("the order of the rules", () => {
  const rules = [rule("a", Way.Exit), rule("b", Way.Exit), rule("c", Way.Block)];

  it("a rule moves one place up or down; at the edge it stays", () => {
    expect(moved(rules, null, "b", -1)).toEqual(["b", "a", "c"]);
    expect(moved(rules, ["b", "a", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(moved(rules, null, "c", 1)).toEqual(["a", "b", "c"]);
  });

  it("is shown as staged, and back where it was is no change", () => {
    expect(orderedRules(rules, ["c", "a", "b"]).map(shown => shown.id)).toEqual(["c", "a", "b"]);
    expect(orderChanged(rules, ["a", "b", "c"])).toBe(false);
    expect(orderChanged(rules, ["c", "a", "b"])).toBe(true);
  });

  it("a staged order that no longer fits the rules is not used", () => {
    expect(orderedRules(rules, ["a", "b"]).map(shown => shown.id)).toEqual(["a", "b", "c"]);
  });
});

describe("the shares of the exits", () => {
  it("add up to 100 as the server makes them", () => {
    expect(sharesOf([exit(1, 1), exit(2, 1), exit(3, 1)])).toEqual([34, 33, 33]);
    expect(sharesOf([exit(1, 0)])).toEqual([0]);
  });

  it("move by steps within what the server takes; 0 is the exit switched off", () => {
    expect(stepped(97, 5)).toBe(100);
    expect(stepped(3, -5)).toBe(0);
    expect(allOff([0, 0])).toBe(true);
    expect(allOff([0, 100])).toBe(false);
  });

  it("one press switches an exit off, its share to the largest; back on, it takes a step from it", () => {
    expect(toggledOff([55, 17, 28], 1)).toEqual([72, 0, 28]);
    expect(toggledOff([72, 0, 28], 1)).toEqual([67, 5, 28]);
    expect(toggledOff([100], 0)).toEqual([100]);
    expect(toggledOff([100, 0], 0)).toEqual([100, 0]);
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
