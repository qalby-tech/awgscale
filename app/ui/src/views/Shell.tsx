// The joined app: tabs for everybody (Подключение, Настройки) and, for an owner, Сеть and Журнал.
import { useState } from "react";

import { type ProfileShown } from "../types";
import { Home } from "./Home";
import { TabIcon, TabPicture } from "./icons";
import { OwnerLater } from "./OwnerLater";
import { Settings } from "./Settings";

enum Tab {
  Connection = "connection",
  Network = "network",
  Journal = "journal",
  Settings = "settings",
}

interface TabShown {
  tab: Tab;
  label: string;
  icon: TabIcon;
}

const EVERYBODY: TabShown[] = [
  { tab: Tab.Connection, label: "Подключение", icon: TabIcon.Connection },
  { tab: Tab.Settings, label: "Настройки", icon: TabIcon.Settings },
];
const OWNER: TabShown[] = [
  { tab: Tab.Connection, label: "Подключение", icon: TabIcon.Connection },
  { tab: Tab.Network, label: "Сеть", icon: TabIcon.Network },
  { tab: Tab.Journal, label: "Журнал", icon: TabIcon.Journal },
  { tab: Tab.Settings, label: "Настройки", icon: TabIcon.Settings },
];

interface ShellProps {
  profile: ProfileShown;
  onLeft: () => void;
}

export function Shell({ profile, onLeft }: ShellProps) {
  const [tab, setTab] = useState(Tab.Connection);
  const [owner, setOwner] = useState(profile.owner);
  const tabs = owner ? OWNER : EVERYBODY;

  function page() {
    switch (tab) {
      case Tab.Connection:
        return <Home profile={profile} onOwner={setOwner} />;
      case Tab.Network:
        return <OwnerLater host={profile.host} what="Сеть" />;
      case Tab.Journal:
        return <OwnerLater host={profile.host} what="Журнал" />;
      case Tab.Settings:
        return <Settings profile={profile} onLeft={onLeft} />;
    }
  }

  return (
    <div className="shell">
      <div className="page">{page()}</div>
      <nav
        className="tabs"
        aria-label="Разделы"
        style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
      >
        {tabs.map(shown => (
          <button
            key={shown.tab}
            type="button"
            className="tab"
            aria-current={shown.tab === tab ? "page" : undefined}
            onClick={() => setTab(shown.tab)}
          >
            <TabPicture path={shown.icon} />
            {shown.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
