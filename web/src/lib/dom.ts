/** Names the browser gives to keys and events, so the code compares against them by name. */

export enum Key {
  Escape = "Escape",
  Enter = "Enter",
  Space = " ",
}

export enum PointerEventType {
  Cancel = "pointercancel",
}

/** Is this element a field the user types text into. */
export const isTextField = (element: Element | null): boolean => element instanceof HTMLInputElement;
