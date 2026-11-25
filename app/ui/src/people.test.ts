import { describe, expect, it } from "vitest";

import { type Person } from "./types";
import { spared } from "./views/People";

function person(name: string, owner = false, me = false): Person {
  return { name, online: false, paused: false, seen: null, expires_at: null, exit: null, group: null, owner, me };
}

describe("who may not be paused or removed from the app", () => {
  it("never this device", () => {
    expect(spared(person("phone", true, true), ["phone", "laptop"])).toBe(true);
  });

  it("never the only owner left; another owner may be", () => {
    expect(spared(person("laptop", true), ["laptop"])).toBe(true);
    expect(spared(person("laptop", true), ["phone", "laptop"])).toBe(false);
  });

  it("anybody else may be", () => {
    expect(spared(person("ann"), ["phone"])).toBe(false);
  });
});
