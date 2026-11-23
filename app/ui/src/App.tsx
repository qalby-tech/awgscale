import { useCallback, useEffect, useState } from "react";

import { backend, shownError } from "./backend";
import { type Language, languageOf, TEXTS, TextsContext } from "./i18n";
import { type ProfileShown } from "./types";
import { Shell } from "./views/Shell";
import { Start } from "./views/Start";

enum Phase {
  Loading,
  Start,
  Joined,
  Broken,
}

export function App() {
  const [phase, setPhase] = useState(Phase.Loading);
  const [profile, setProfile] = useState<ProfileShown | null>(null);
  const [language, setLanguage] = useState<Language>(languageOf(null));
  const [error, setError] = useState("");
  // an awgscale://join link the phone opened the app with (Android's intent), not yet shown to the person
  const [opened, setOpened] = useState<string | null>(null);

  const speak = useCallback((next: Language) => {
    setLanguage(next);
    document.documentElement.lang = next;
    void backend.useLanguage(next).catch(() => undefined);
  }, []);

  useEffect(() => {
    backend
      .settings()
      .then(chosen => speak(languageOf(chosen.language)))
      .catch(() => speak(languageOf(null)));
    backend
      .profile()
      .then(saved => {
        setProfile(saved);
        setPhase(saved == null ? Phase.Start : Phase.Joined);
      })
      .catch((failure: unknown) => {
        setError(shownError(failure, TEXTS[languageOf(null)].somethingWrong));
        setPhase(Phase.Broken);
      });
  }, [speak]);

  // The link is only put in front of the person: nothing is asked of its host before they press a button
  // (any app or web page can open such a link). Asked when the app starts and whenever it comes to the front.
  useEffect(() => {
    async function take() {
      if (document.hidden) {
        return;
      }
      const given = await backend.openedLink().catch(() => null);
      if (given != null) {
        setOpened(given);
      }
    }
    void take();
    document.addEventListener("visibilitychange", take);
    return () => document.removeEventListener("visibilitychange", take);
  }, []);

  function joined(next: ProfileShown) {
    setProfile(next);
    setPhase(Phase.Joined);
  }

  function left() {
    setProfile(null);
    setPhase(Phase.Start);
  }

  function page() {
    if (phase === Phase.Broken) {
      return <main className="screen center muted">{error}</main>;
    }
    if (phase === Phase.Joined && profile != null) {
      return (
        <Shell
          profile={profile}
          opened={opened}
          onForgetOpened={() => setOpened(null)}
          onLeft={left}
          onLanguage={speak}
        />
      );
    }
    if (phase === Phase.Start) {
      return <Start opened={opened} onOpenedTaken={() => setOpened(null)} onJoined={joined} />;
    }
    return <main className="screen" aria-busy="true" />;
  }

  return <TextsContext.Provider value={TEXTS[language]}>{page()}</TextsContext.Provider>;
}
