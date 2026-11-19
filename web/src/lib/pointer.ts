import { isTextField } from "./dom";

/** Whether a pointer is pressed right now: a list is not rebuilt under it, or the click would be lost. */
let down = false;
let bound = false;

export function trackPointer(): void {
  if (bound || typeof document === "undefined") return;
  bound = true;
  document.addEventListener(
    "pointerdown",
    () => {
      down = true;
    },
    true,
  );
  for (const type of ["pointerup", "pointercancel"])
    document.addEventListener(
      type,
      () => {
        down = false;
      },
      true,
    );
}

export const pointerIsDown = (): boolean => down;

/** Is the focus in a text field inside the element with this id. */
export function typingIn(id: string): boolean {
  const active = document.activeElement;
  const box = document.getElementById(id);
  return !!active && !!box && isTextField(active) && box.contains(active);
}

export function typingAnywhere(): boolean {
  const active = document.activeElement;
  return !!active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
}
