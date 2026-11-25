// «Пригласить по QR»: a new invitation into the network, for how many devices, how many days and which group;
// then its QR code for another phone's camera, and its link to send.
import { useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import { type Invitation, type OwnerGroup } from "../types";

const USES = [1, 3, 5, 10];
const DAYS = [1, 7, 30];
const DEFAULT_USES = 1;
const DEFAULT_DAYS = 7;

interface InviteSheetProps {
  groups: OwnerGroup[];
  onClose: () => void;
}

export function InviteSheet({ groups, onClose }: InviteSheetProps) {
  const texts = useTexts();
  const [uses, setUses] = useState(DEFAULT_USES);
  const [days, setDays] = useState(DEFAULT_DAYS);
  const [group, setGroup] = useState<string | null>(null);
  const [making, setMaking] = useState(false);
  const [made, setMade] = useState<Invitation | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const named = groups.filter(shown => shown.id != null && !shown.space);

  async function make() {
    setMaking(true);
    setError("");
    try {
      setMade(await backend.invite({ group, uses, days }));
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    } finally {
      setMaking(false);
    }
  }

  function copy(link: string) {
    void navigator.clipboard
      .writeText(link)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  }

  return (
    <>
      <div className="dim" onClick={onClose} />
      <section className="sheet" role="dialog" aria-label={texts.net.invitation}>
        <span className="grip" />
        <h2>{texts.net.invitation}</h2>
        {made == null ? (
          <>
            <Choice title={texts.net.devices} values={USES} chosen={uses} onChoose={setUses} />
            <Choice title={texts.net.days} values={DAYS} chosen={days} onChoose={setDays} />
            {named.length > 0 && (
              <div className="stack tight">
                <span className="small muted">{texts.net.group}</span>
                <div className="chips">
                  <button type="button" className="chip" aria-pressed={group == null} onClick={() => setGroup(null)}>
                    {texts.net.noGroup}
                  </button>
                  {named.map(shown => (
                    <button
                      key={shown.id}
                      type="button"
                      className="chip"
                      aria-pressed={group === shown.id}
                      onClick={() => setGroup(shown.id)}
                    >
                      {shown.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {error.length > 0 && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <button type="button" className="primary" disabled={making} onClick={() => void make()}>
              {texts.net.make}
            </button>
          </>
        ) : (
          <>
            <img className="code-picture" src={`data:image/png;base64,${made.png}`} alt={made.link} />
            <p className="small muted">{texts.net.inviteHint}</p>
            <p className="mono small link">{made.link}</p>
            <button type="button" className="ghost" onClick={() => copy(made.link)}>
              {copied ? texts.net.copied : texts.net.copy}
            </button>
          </>
        )}
        <button type="button" className="ghost" onClick={onClose}>
          {texts.close}
        </button>
      </section>
    </>
  );
}

interface ChoiceProps {
  title: string;
  values: number[];
  chosen: number;
  onChoose: (value: number) => void;
}

function Choice({ title, values, chosen, onChoose }: ChoiceProps) {
  return (
    <div className="stack tight">
      <span className="small muted">{title}</span>
      <div className="chips">
        {values.map(value => (
          <button
            key={value}
            type="button"
            className="chip"
            aria-pressed={value === chosen}
            onClick={() => onChoose(value)}
          >
            {value}
          </button>
        ))}
      </div>
    </div>
  );
}
