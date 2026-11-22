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
      return <Shell profile={profile} onLeft={left} onLanguage={speak} />;
    }
    if (phase === Phase.Start) {
      return <Start onJoined={joined} />;
    }
    return <main className="screen" aria-busy="true" />;
  }

  return <TextsContext.Provider value={TEXTS[language]}>{page()}</TextsContext.Provider>;
}
