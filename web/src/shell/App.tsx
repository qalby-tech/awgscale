"use client";

import { useEffect } from "react";

import { trackPointer } from "@/lib/pointer";
import { PAGES } from "@/views";
import { sectionOf, Session, start, useCore } from "@/store/core";
import { Menu } from "@/ui/Menu";
import { Toasts } from "@/ui/Toasts";

import { Login } from "./Login";
import { PRODUCT, SECTIONS } from "./sections";
import { Sidebar, TopBar } from "./Sidebar";

export function App() {
  const session = useCore(state => state.session);
  const route = useCore(state => state.route);
  const dimmed = useCore(state => state.locks.length > 0);
  const Page = PAGES[route];

  useEffect(() => {
    trackPointer();
    start();
  }, []);

  // a section opens from its top, under its own title
  useEffect(() => {
    if (session !== Session.App) return;
    const title = SECTIONS.find(section => section.section === sectionOf(route))?.title ?? SECTIONS[0].title;
    document.title = PRODUCT + " — " + title;
    window.scrollTo(0, 0);
  }, [session, route]);

  return (
    <>
      <Login shown={session === Session.Login} />
      <div id="app" className="app" hidden={session !== Session.App}>
        <TopBar />
        <Sidebar />
        <main className="main">
          <div className={"wrap" + (dimmed ? " busy" : "")} id="page">
            {session === Session.App ? <Page key={route} /> : null}
          </div>
        </main>
      </div>
      <Menu />
      <Toasts />
    </>
  );
}
