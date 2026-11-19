/** The one popup menu of the page: opened under a button, closed by a choice, Escape or a click elsewhere. */
import type { MouseEvent } from "react";
import { create } from "zustand";

export interface MenuItem {
  label: string;
  /** A short note at the right edge: the current value, an address. */
  sub?: string;
  danger?: boolean;
  run: () => void;
}

interface OpenMenu {
  anchor: HTMLElement;
  items: MenuItem[];
  emptyText: string;
}

export const useMenu = create<{ open: OpenMenu | null }>(() => ({ open: null }));

const DEFAULT_EMPTY = "Пусто";

export function openMenu(anchor: HTMLElement, items: MenuItem[], emptyText = DEFAULT_EMPTY): void {
  closeMenu();
  anchor.setAttribute("aria-expanded", "true");
  useMenu.setState({ open: { anchor, items, emptyText } });
}

export function closeMenu(refocus = false): void {
  const open = useMenu.getState().open;
  if (!open) return;
  useMenu.setState({ open: null });
  open.anchor.removeAttribute("aria-expanded");
  if (refocus && document.contains(open.anchor)) open.anchor.focus();
}

export const menuIsOpen = (): boolean => !!useMenu.getState().open;
export const menuAnchor = (): HTMLElement | null => useMenu.getState().open?.anchor ?? null;

/** Props for a button that opens a menu; the items are asked for at the moment of the click. */
export function menuButton(items: () => MenuItem[], emptyText?: string) {
  return {
    "aria-haspopup": "menu" as const,
    onClick: (event: MouseEvent<HTMLElement>) => {
      const button = event.currentTarget;
      if (menuAnchor() === button) closeMenu();
      else openMenu(button, items(), emptyText);
    },
  };
}
