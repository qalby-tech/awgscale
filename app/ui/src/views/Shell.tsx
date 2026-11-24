// The joined app: tabs for everybody (Подключение, Настройки) and, for an owner, Сеть and Журнал. The
// connection and the settings stay mounted behind the other tabs: coming back to one shows it whole at once,
// as it was, while it asks again in the background (built anew it showed half of itself until answered).
import { useState } from "react";

import { type Language, useTexts } from "../i18n";
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

const EVERYBODY: Tab[] = [Tab.Connection, Tab.Settings];
const OWNER: Tab[] = [Tab.Connection, Tab.Network, Tab.Journal, Tab.Settings];

const ICONS: Record<Tab, TabIcon> = {
  [Tab.Connection]: TabIcon.Connection,
  [Tab.Network]: TabIcon.Network,
  [Tab.Journal]: TabIcon.Journal,
  [Tab.Settings]: TabIcon.Settings,
};

interface ShellProps {
  profile: ProfileShown;
  // a join link the phone opened the app with while it is in a network already
  opened: string | null;
  onForgetOpened: () => void;
  onLeft: () => void;
  onLanguage: (language: Language) => void;
}

export function Shell({ profile, opened, onForgetOpened, onLeft, onLanguage }: ShellProps) {
  const texts = useTexts();
  const [tab, setTab] = useState(Tab.Connection);
  const [owner, setOwner] = useState(profile.owner);
  const tabs = owner ? OWNER : EVERYBODY;

  function ownerPage() {
    switch (tab) {
      case Tab.Network:
        return <OwnerLater host={profile.host} what={texts.tabs.network} />;
      case Tab.Journal:
        return <OwnerLater host={profile.host} what={texts.tabs.journal} />;
      default:
        return null;
    }
  }

  return (
    <div className="shell">
      <div className="page">
        {opened != null && (
          <div className="note" role="status">
            <p>{texts.linkWhileJoined}</p>
            <p className="mono small">{opened}</p>
            <button type="button" onClick={onForgetOpened}>
              {texts.close}
            </button>
          </div>
        )}
        <div hidden={tab !== Tab.Connection}>
          <Home profile={profile} shown={tab === Tab.Connection} onOwner={setOwner} />
        </div>
        <div hidden={tab !== Tab.Settings}>
          <Settings profile={profile} onLeft={onLeft} onLanguage={onLanguage} />
        </div>
        {ownerPage()}
      </div>
      <nav
        className="tabs"
        aria-label={texts.tabs.settings}
        style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
      >
        {tabs.map(shown => (
          <button
            key={shown}
            type="button"
            className="tab"
            aria-current={shown === tab ? "page" : undefined}
            onClick={() => setTab(shown)}
          >
            <TabPicture path={ICONS[shown]} />
            {texts.tabs[shown]}
          </button>
        ))}
      </nav>
    </div>
  );
}
