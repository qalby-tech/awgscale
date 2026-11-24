// The protection a person chooses for themselves on the connection's screen. The network filters a device
// that chose nothing as its group (as everybody outside one: Обычная, agent/adguard/profiles.py effective);
// the app no longer offers «as my group»: it shows the level that applies and sends only a level, never
// "auto", so an existing device keeps what it has until the person picks another.
import { Filter } from "./types";

// The levels offered, in their order
export const OWN_LEVELS: readonly Filter[] = [
  Filter.Standard,
  Filter.Pro,
  Filter.ProPlus,
  Filter.Ultimate,
  Filter.Kids,
];

// The levels the explanation names, in their order (Выключена is kept by the network but no longer offered)
export const EXPLAINED_LEVELS: readonly Filter[] = [
  Filter.Off,
  Filter.Standard,
  Filter.Pro,
  Filter.ProPlus,
  Filter.Ultimate,
  Filter.Kids,
];

// The chips: the levels offered, and the one that applies first when it is not among them (Выключена, set
// earlier), so that one of them is always the chosen one
export function levelsShown(applies: Filter): readonly Filter[] {
  return OWN_LEVELS.includes(applies) ? OWN_LEVELS : [applies, ...OWN_LEVELS];
}
