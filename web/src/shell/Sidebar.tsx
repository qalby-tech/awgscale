"use client";

import { useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";

import { hrefOf, logout, problems, sectionOf, useCore } from "@/store/core";
import { Dot, DotKind } from "@/ui/controls";
import { Icon, IconName } from "@/ui/Icon";

import { BrandMark } from "./BrandMark";
import { PRODUCT, SECTIONS } from "./sections";

export function Sidebar() {
  const current = useCore(state => sectionOf(state.route));
  const bad = useCore(useShallow(state => problems(state))).length > 0;
  const nav = useRef<HTMLDivElement>(null);

  // on a narrow screen the menu is a strip that scrolls: the current section stays in view
  useEffect(() => {
    nav.current?.querySelector('a[aria-current="page"]')?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [current]);

  return (
    <nav className="side" aria-label="Разделы панели">
      <div className="side-top">
        <div className="brand">
          <div className="brand-mark" id="brandMark">
            <BrandMark />
          </div>
          <div className="brand-name">{PRODUCT}</div>
        </div>
        <div className="nav" id="nav" ref={nav}>
          {SECTIONS.map(section => (
            <a
              key={section.section}
              href={hrefOf(section.route)}
              data-sec={section.section}
              aria-current={section.section === current ? "page" : undefined}
            >
              <Icon name={section.icon} />
              <span>{section.title}</span>
            </a>
          ))}
        </div>
      </div>
      <div className="side-foot">
        <div className="status" id="status" role="status">
          <Dot kind={bad ? DotKind.Crit : DotKind.Good} />
          <span>{bad ? "Есть проблемы" : "Всё работает"}</span>
        </div>
        <button type="button" className="side-btn" id="logoutBtn" aria-label="Выйти" onClick={() => void logout()}>
          <Icon name={IconName.Logout} />
          <span>Выйти</span>
        </button>
      </div>
    </nav>
  );
}
