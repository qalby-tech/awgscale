// The protection's chips: no «as my group», the level that applies always among them.
import { describe, expect, it } from "vitest";

import { EXPLAINED_LEVELS, levelsShown, OWN_LEVELS } from "./protection";
import { Filter } from "./types";

describe("levelsShown", () => {
  it("offers Standard first, then the stricter levels and Kids", () => {
    expect(levelsShown(Filter.Standard)).toEqual([
      Filter.Standard,
      Filter.Pro,
      Filter.ProPlus,
      Filter.Ultimate,
      Filter.Kids,
    ]);
  });

  it("holds the level that applies, the group's included", () => {
    for (const applies of Object.values(Filter)) {
      expect(levelsShown(applies)).toContain(applies);
    }
  });

  it("shows Off only while it applies", () => {
    expect(OWN_LEVELS).not.toContain(Filter.Off);
    expect(levelsShown(Filter.Off)[0]).toBe(Filter.Off);
  });
});

describe("the explanation", () => {
  it("names every level once", () => {
    expect([...EXPLAINED_LEVELS].sort()).toEqual(Object.values(Filter).sort());
  });
});
