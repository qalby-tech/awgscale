"use client";

import { useEffect, useLayoutEffect, useRef } from "react";

import { closeMenu, useMenu, type MenuItem } from "./menu";
import { Key } from "@/lib/dom";

const EDGE = 8;
const GAP = 4;

/** A choice closes the menu first: what it runs may open another one in its place. */
function choose(item: MenuItem): void {
  closeMenu();
  item.run();
}

export function Menu() {
  const open = useMenu(state => state.open);
  const box = useRef<HTMLDivElement>(null);

  // under the button, kept inside the window; above it when there is no room below
  useLayoutEffect(() => {
    const menu = box.current;
    if (!menu || !open) return;
    const rect = open.anchor.getBoundingClientRect();
    const menuWidth = menu.offsetWidth;
    const menuHeight = menu.offsetHeight;
    const x = Math.max(EDGE, Math.min(rect.right - menuWidth, innerWidth - menuWidth - EDGE));
    let y = rect.bottom + GAP;
    if (y + menuHeight > innerHeight - EDGE) y = Math.max(EDGE, rect.top - menuHeight - GAP);
    menu.style.left = x + "px";
    menu.style.top = y + "px";
    menu.querySelector("button")?.focus({ preventScroll: true });
  }, [open]);

  useEffect(() => {
    const inside = (target: EventTarget | null) =>
      !!box.current && target instanceof Node && box.current.contains(target);
    const onPointer = (event: PointerEvent) => {
      const opened = useMenu.getState().open;
      if (opened && !inside(event.target) && !(event.target instanceof Node && opened.anchor.contains(event.target)))
        closeMenu();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === Key.Escape && useMenu.getState().open) closeMenu(true);
    };
    const onResize = () => closeMenu();
    // a text field scrolling its own text (a selection let go) moves nothing on the page: the menu stays
    const onScroll = (event: Event) => {
      if (useMenu.getState().open && !inside(event.target) && !(event.target instanceof HTMLInputElement)) closeMenu();
    };
    document.addEventListener("pointerdown", onPointer, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer, true);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, []);

  return (
    <div id="menu" className="menu" role="menu" hidden={!open} ref={box}>
      {open?.items.map((item, index) => (
        <button
          key={index}
          type="button"
          role="menuitem"
          className={item.danger ? "danger" : ""}
          onClick={() => choose(item)}
        >
          <span>{item.label}</span>
          {item.sub ? <span className="sub">{item.sub}</span> : null}
        </button>
      ))}
      {open && !open.items.length ? <div className="menu-empty">{open.emptyText}</div> : null}
    </div>
  );
}
