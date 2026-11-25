// The people of one group (or of none), from «Люди» of «Сеть»: who is online, who is paused; a person can be
// paused, let back or removed for good (after a question). The device that asks, and the last owner, are never
// paused or removed: the server refuses that too.
import { useCallback, useEffect, useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import { NO_GROUP, type OwnerGroup, type Person } from "../types";

interface GroupPeopleProps {
  group: OwnerGroup;
  owners: string[];
  onBack: () => void;
  // something changed: the network's counts are asked again
  onChanged: () => void;
}

// Neither this device nor the only owner left may be paused or removed.
export function spared(person: Person, owners: string[]): boolean {
  return person.me || (person.owner && owners.filter(owner => owner !== person.name).length === 0);
}

export function GroupPeople({ group, owners, onBack, onChanged }: GroupPeopleProps) {
  const texts = useTexts();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  // the person whose removal is being asked about
  const [asking, setAsking] = useState<string | null>(null);
  const name = group.id == null ? texts.net.noGroup : group.name;

  const load = useCallback(() => {
    backend
      .people(group.id ?? NO_GROUP)
      .then(answer => {
        setPeople(answer?.people ?? []);
        setError("");
      })
      .catch((failure: unknown) => setError(shownError(failure, texts.somethingWrong)));
  }, [group.id, texts]);

  useEffect(load, [load]);

  function presence(person: Person): string {
    if (person.paused) {
      return texts.net.paused;
    }
    return person.online ? texts.net.online : texts.net.offline;
  }

  async function act(person: Person, work: () => Promise<null>) {
    setBusy(person.name);
    setError("");
    try {
      await work();
      onChanged();
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    } finally {
      setBusy(null);
      setAsking(null);
      load();
    }
  }

  return (
    <div className="stack tight">
      <div className="row">
        <button type="button" className="ghost back" onClick={onBack}>
          ‹ {texts.net.groups}
        </button>
        <span className="grow strong">{name}</span>
      </div>
      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {people == null && <section className="card" aria-busy="true" />}
      {people != null && people.length === 0 && <p className="note">{texts.net.nobody}</p>}
      {people != null &&
        people.map(person => (
          <div key={person.name} className={`person${person.paused ? " paused" : ""}`}>
            <span className={`dot ${person.online ? "up" : ""}`} />
            <span className="grow">
              <span className="strong">{person.name}</span>
              <span className="small muted">
                {[
                  person.me ? texts.net.thisDevice : undefined,
                  person.owner ? texts.net.owner : undefined,
                  presence(person),
                ]
                  .filter(part => part != null)
                  .join(" · ")}
              </span>
            </span>
            {asking === person.name ? (
              <span className="confirm-inline">
                <button
                  type="button"
                  className="danger"
                  disabled={busy != null}
                  onClick={() => void act(person, () => backend.removePeople([person.name]))}
                >
                  {texts.net.removeYes}
                </button>
                <button type="button" className="ghost" onClick={() => setAsking(null)}>
                  {texts.cancel}
                </button>
              </span>
            ) : (
              !spared(person, owners) && (
                <span className="person-actions">
                  <button
                    type="button"
                    className="ghost small"
                    disabled={busy != null}
                    aria-label={`${person.paused ? texts.net.resume : texts.net.pause} ${person.name}`}
                    onClick={() => void act(person, () => backend.pausePeople([person.name], !person.paused))}
                  >
                    {person.paused ? texts.net.resume : texts.net.pause}
                  </button>
                  <button
                    type="button"
                    className="ghost small danger-text"
                    disabled={busy != null}
                    aria-label={`${texts.net.remove} ${person.name}`}
                    onClick={() => setAsking(person.name)}
                  >
                    {texts.net.remove}
                  </button>
                </span>
              )
            )}
          </div>
        ))}
      {asking != null && <p className="note small">{texts.net.removeNote(asking)}</p>}
    </div>
  );
}
