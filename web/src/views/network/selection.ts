/**
 * What is selected on the map of the network. It outlives the section: the
 * admin may look something up elsewhere and come back to the same thing.
 */
import { create } from "zustand";

/** The kinds of things the map shows; a selection is "<kind>:<key>". */
export enum SelKind {
  Group = "g",
  User = "u",
  Exit = "x",
}

export type Selection = string;

export const groupSel = (key: string): Selection => SelKind.Group + ":" + key;
export const userSel = (name: string): Selection => SelKind.User + ":" + name;
export const exitSel = (index: number): Selection => SelKind.Exit + ":" + index;

export const kindOf = (selection: Selection): SelKind => selection.slice(0, 1) as SelKind;
export const keyOf = (selection: Selection): string => selection.slice(2);

export const useSelection = create<{ selection: Selection | null }>(() => ({ selection: null }));

export const mapSelect = (selection: Selection | null): void => useSelection.setState({ selection });
