import type { ReactNode } from "react";

import type { Route } from "@/lib/types";
import { hrefOf, useCore } from "@/store/core";

export type Tabs = ReadonlyArray<readonly [Route, string]>;

/** The title of a section, its tabs and the buttons at the right. */
export function PageHead({ title, tabs, children }: { title: string; tabs?: Tabs; children?: ReactNode }) {
  const route = useCore(state => state.route);
  return (
    <header className="head">
      <div className="head-l">
        <h1>{title}</h1>
        {tabs ? (
          <nav className="tabs" aria-label="Вкладки раздела">
            {tabs.map(([target, text]) => (
              <a key={target} href={hrefOf(target)} aria-current={target === route ? "page" : undefined}>
                {text}
              </a>
            ))}
          </nav>
        ) : null}
      </div>
      {children}
    </header>
  );
}
