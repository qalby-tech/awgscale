"use client";

import { useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";

import { hrefOf, logout, problems, sectionOf, useCore } from "@/store/core";
import { Dot, DotKind } from "@/ui/controls";
import { Icon, IconName } from "@/ui/Icon";

import { BrandMark } from "./BrandMark";
import { PRODUCT, SECTIONS } from "./sections";

/** The bar along the top: the product, the sections, whether everything works and how many are online, the way out. */
export function TopBar() {
  const bad = useCore(useShallow(state => problems(state))).length > 0;
  const online = useCore(state => state.state?.totals?.online);
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark" id="brandMark">
          <BrandMark />
        </div>
        <div className="brand-name">{PRODUCT}</div>
      </div>
      <Sidebar />
      <div className="spacer" />
      <div className={"pill" + (bad ? " bad" : "")}>
        <div className="status" id="status" role="status">
          <Dot kind={bad ? DotKind.Crit : DotKind.Good} />
          <span>{bad ? "Есть проблемы" : "Всё работает"}</span>
        </div>
        {online === undefined ? null : <span className="pill-more">· {online} онлайн</span>}
      </div>
      <button
        type="button"
        className="icon-btn"
        id="logoutBtn"
        aria-label="Выйти"
        title="Выйти"
        onClick={() => void logout()}
      >
        <Icon name={IconName.Logout} />
      </button>
    </header>
  );
}

/** The sections: tabs in the bar along the top, a bar of tabs at the bottom on a phone. */
export function Sidebar() {
  const current = useCore(state => sectionOf(state.route));
  const nav = useRef<HTMLDivElement>(null);

  // on a phone the bar may not fit: the current section stays in view
  useEffect(() => {
    nav.current?.querySelector('a[aria-current="page"]')?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [current]);

  return (
    <nav className="side" aria-label="Разделы панели">
      <div className="nav" id="nav" ref={nav}>
        {SECTIONS.map(section => (
          <a
            key={section.section}
            href={hrefOf(section.route)}
            data-sec={section.section}
            data-short={section.short}
            title={section.title}
            aria-current={section.section === current ? "page" : undefined}
          >
            <Icon name={section.icon} />
            <span className="vh">{section.title}</span>
          </a>
        ))}
      </div>
    </nav>
  );
}
