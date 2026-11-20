import { useEffect, useState } from "react";

import { backend, shownError } from "./backend";
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
  const [error, setError] = useState("");

  useEffect(() => {
    backend
      .profile()
      .then(saved => {
        setProfile(saved);
        setPhase(saved == null ? Phase.Start : Phase.Joined);
      })
      .catch((failure: unknown) => {
        setError(shownError(failure));
        setPhase(Phase.Broken);
      });
  }, []);

  function joined(next: ProfileShown) {
    setProfile(next);
    setPhase(Phase.Joined);
  }

  function left() {
    setProfile(null);
    setPhase(Phase.Start);
  }

  if (phase === Phase.Broken) {
    return <main className="screen center muted">{error}</main>;
  }
  if (phase === Phase.Joined && profile != null) {
    return <Shell profile={profile} onLeft={left} />;
  }
  if (phase === Phase.Start) {
    return <Start onJoined={joined} />;
  }
  return <main className="screen" aria-busy="true" />;
}
