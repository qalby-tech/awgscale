// The protection a person chooses for themselves on the connection's screen. The network filters a device
// that chose nothing as its group (as everybody outside one: Обычная, agent/adguard/profiles.py effective);
// the app no longer offers «as my group»: it shows the level that applies and sends only a level, never
// "auto", so an existing device keeps what it has until the person picks another. A children's group is the
// exception: an adult chose its level, the device shows it and offers none (the network refuses a change).
import { Filter, type Me } from "./types";

// The levels offered, in their order, which is also the explanation's: Выключена first, then stricter and
// stricter, Детская last
export const LEVELS: readonly Filter[] = [
  Filter.Off,
  Filter.Standard,
  Filter.Pro,
  Filter.ProPlus,
  Filter.Ultimate,
  Filter.Kids,
];

// The person as the network holds them once it took the level they chose for themselves
export function withLevel(me: Me, level: Filter): Me {
  if (me.filter == null) {
    return me;
  }
  return { ...me, filter: { ...me.filter, applies: level, own: level } };
}

// How the last of a run of choices came out
export interface Settled<Choice> {
  // the one the person made last
  chosen: Choice;
  // whether the network took it
  taken: boolean;
  // what went wrong when it did not
  failure: unknown;
  // the last choice of the run the network took (the last one itself when taken), null when it took none:
  // what the network holds now
  held: Choice | null;
}

interface Waiting<Choice> {
  choice: Choice;
  answer: (settled: Settled<Choice> | null) => void;
}

// Choices sent to the network one at a time, in the order they were made, and only the latest one waiting:
// a choice made while another is under way replaces whatever was still waiting, so the network ends on the
// last one and never on an earlier answer that came late. Each choice is answered with how the run came out
// when it was the last of it, or with null when a later one replaced it.
export class LatestChoice<Choice> {
  readonly #send: (choice: Choice) => Promise<unknown>;
  #waiting: Waiting<Choice> | null = null;
  #sending = false;

  constructor(send: (choice: Choice) => Promise<unknown>) {
    this.#send = send;
  }

  choose(choice: Choice): Promise<Settled<Choice> | null> {
    this.#waiting?.answer(null);
    return new Promise(answer => {
      this.#waiting = { choice, answer };
      if (!this.#sending) {
        void this.#run();
      }
    });
  }

  async #run(): Promise<void> {
    this.#sending = true;
    let held: Choice | null = null;
    while (this.#waiting != null) {
      const { choice, answer } = this.#waiting;
      this.#waiting = null;
      let taken = true;
      let failure: unknown = null;
      try {
        await this.#send(choice);
        held = choice;
      } catch (error) {
        taken = false;
        failure = error;
      }
      if (this.#waiting == null) {
        this.#sending = false;
        answer({ chosen: choice, taken, failure, held });
        return;
      }
      answer(null);
    }
    this.#sending = false;
  }
}
