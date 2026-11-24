// The protection's chips: every level, Выключена among them, no «as my group»; choices sent one at a time,
// the latest one winning.
import { describe, expect, it } from "vitest";

import { LatestChoice, LEVELS, withLevel } from "./protection";
import { Filter, type Me } from "./types";

describe("the levels", () => {
  it("offer Off first, then the stricter levels and Kids", () => {
    expect(LEVELS).toEqual([Filter.Off, Filter.Standard, Filter.Pro, Filter.ProPlus, Filter.Ultimate, Filter.Kids]);
  });

  it("name every level once, the one that applies always among them", () => {
    expect([...LEVELS].sort()).toEqual(Object.values(Filter).sort());
  });
});

const ME: Me = {
  name: "test_laptop",
  address: "172.16.17.23",
  owner: false,
  expires_at: null,
  exit: { pinned: null, group: null, name: null },
  group: null,
  filter: { applies: Filter.Standard, own: null, locked: false },
  route: null,
};

describe("withLevel", () => {
  it("holds the level as the person's own", () => {
    expect(withLevel(ME, Filter.Off).filter).toEqual({ applies: Filter.Off, own: Filter.Off, locked: false });
  });

  it("leaves a server older than the choice of one's filtering as it is", () => {
    const older = { ...ME, filter: null };
    expect(withLevel(older, Filter.Pro)).toBe(older);
  });
});

// A network whose every answer waits until the test gives it
class Network {
  readonly asked: Filter[] = [];
  readonly #answers: ((refusal: string | null) => void)[] = [];

  send = (choice: Filter): Promise<void> => {
    this.asked.push(choice);
    return new Promise((resolve, reject) => {
      this.#answers.push(refusal => (refusal == null ? resolve() : reject(refusal)));
    });
  };

  // the oldest question not answered yet is answered, and what follows from it is let happen
  async answer(refusal: string | null = null): Promise<void> {
    const next = this.#answers.shift();
    if (next == null) {
      throw new Error("nothing was asked");
    }
    next(refusal);
    await new Promise(resolve => setTimeout(resolve, 0));
  }
}

describe("LatestChoice", () => {
  it("sends a choice at once and says the network took it", async () => {
    const network = new Network();
    const chooser = new LatestChoice(network.send);
    const settled = chooser.choose(Filter.Pro);
    expect(network.asked).toEqual([Filter.Pro]);
    await network.answer();
    expect(await settled).toEqual({ chosen: Filter.Pro, taken: true, failure: null, held: Filter.Pro });
  });

  it("says what went wrong, and that the network holds none of it", async () => {
    const network = new Network();
    const chooser = new LatestChoice(network.send);
    const settled = chooser.choose(Filter.Off);
    await network.answer("refused");
    expect(await settled).toEqual({ chosen: Filter.Off, taken: false, failure: "refused", held: null });
  });

  it("sends one at a time and only the latest of those made meanwhile", async () => {
    const network = new Network();
    const chooser = new LatestChoice(network.send);
    const first = chooser.choose(Filter.Pro);
    const second = chooser.choose(Filter.ProPlus);
    const third = chooser.choose(Filter.Kids);
    expect(network.asked).toEqual([Filter.Pro]);
    expect(await second).toBeNull();
    await network.answer();
    expect(await first).toBeNull();
    expect(network.asked).toEqual([Filter.Pro, Filter.Kids]);
    await network.answer();
    expect(await third).toEqual({ chosen: Filter.Kids, taken: true, failure: null, held: Filter.Kids });
  });

  it("goes back to the last one the network took when the latest is refused", async () => {
    const network = new Network();
    const chooser = new LatestChoice(network.send);
    void chooser.choose(Filter.Pro);
    const last = chooser.choose(Filter.Ultimate);
    await network.answer();
    await network.answer("refused");
    expect(await last).toEqual({ chosen: Filter.Ultimate, taken: false, failure: "refused", held: Filter.Pro });
  });

  it("ends on the latest even when an earlier one was refused", async () => {
    const network = new Network();
    const chooser = new LatestChoice(network.send);
    void chooser.choose(Filter.Pro);
    const last = chooser.choose(Filter.Standard);
    await network.answer("refused");
    await network.answer();
    expect(await last).toEqual({ chosen: Filter.Standard, taken: true, failure: null, held: Filter.Standard });
  });

  it("starts afresh after a run has settled", async () => {
    const network = new Network();
    const chooser = new LatestChoice(network.send);
    void chooser.choose(Filter.Pro);
    await network.answer();
    const again = chooser.choose(Filter.Kids);
    expect(network.asked).toEqual([Filter.Pro, Filter.Kids]);
    await network.answer("refused");
    expect(await again).toEqual({ chosen: Filter.Kids, taken: false, failure: "refused", held: null });
  });
});
