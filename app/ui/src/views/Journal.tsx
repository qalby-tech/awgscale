// «Журнал»: the network's changes, the latest first — when, who and what; a failed one is marked. A server
// without the owner's part shows what the app showed before: the panel does it.
import { useCallback, useEffect, useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import { Author, type Note } from "../types";
import { OwnerLater } from "./OwnerLater";

const MILLISECONDS = 1000;

interface JournalProps {
  host: string;
  shown: boolean;
}

export function Journal({ host, shown }: JournalProps) {
  const texts = useTexts();
  const [notes, setNotes] = useState<Note[] | null | undefined>(undefined);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    backend
      .journal()
      .then(answer => {
        setNotes(answer);
        setError("");
      })
      .catch((failure: unknown) => setError(shownError(failure, texts.somethingWrong)));
  }, [texts]);

  useEffect(() => {
    if (shown) {
      load();
    }
  }, [shown, load]);

  if (notes === null) {
    return <OwnerLater host={host} what={texts.tabs.journal} />;
  }

  return (
    <main className="screen">
      <header className="head">
        <h1>{texts.tabs.journal}</h1>
      </header>
      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notes === undefined ? <section className="card" aria-busy="true" /> : <Notes notes={notes} />}
    </main>
  );
}

function Notes({ notes }: { notes: Note[] }) {
  const texts = useTexts();
  if (notes.length === 0) {
    return <p className="note">{texts.journal.empty}</p>;
  }
  const when = new Intl.DateTimeFormat(texts.locale, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

  function author(note: Note): string {
    const known = Object.values(Author).find(each => each === note.who);
    return known == null ? texts.journal.system : texts.journal.authors[known];
  }

  return (
    <ol className="journal">
      {notes.map((note, index) => (
        <li key={notes.length - index} className={note.bad ? "bad" : undefined}>
          <span className="small muted">
            {when.format(new Date(note.t * MILLISECONDS))} · {author(note)}
          </span>
          <span>{note.text}</span>
        </li>
      ))}
    </ol>
  );
}
