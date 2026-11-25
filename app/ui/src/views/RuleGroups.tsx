// Whom a rule is for: everybody, or some groups of people. Chosen in a sheet and staged with the rest of the
// network's changes.
import { useState } from "react";

import { useTexts } from "../i18n";
import { type OwnerGroup, type OwnerRule } from "../types";

interface RuleGroupsSheetProps {
  rule: OwnerRule;
  groups: OwnerGroup[];
  // the ids chosen now (as staged); empty: everybody
  chosen: string[];
  onChoose: (ids: string[]) => void;
  onClose: () => void;
}

export function RuleGroupsSheet({ rule, groups, chosen, onChoose, onClose }: RuleGroupsSheetProps) {
  const texts = useTexts();
  const [picked, setPicked] = useState<string[]>(chosen);
  const named = groups.filter((group): group is OwnerGroup & { id: string } => group.id != null);

  function toggle(id: string) {
    setPicked(picked.includes(id) ? picked.filter(each => each !== id) : [...picked, id]);
  }

  return (
    <>
      <div className="dim" onClick={onClose} />
      <section className="sheet" role="dialog" aria-label={texts.net.forWhom}>
        <span className="grip" />
        <h2>{rule.name}</h2>
        <span className="small muted">{texts.net.forWhomHint}</span>
        <div className="chips wrap" role="group" aria-label={texts.net.forWhom}>
          <button type="button" className="chip" aria-pressed={picked.length === 0} onClick={() => setPicked([])}>
            {texts.net.everybody}
          </button>
          {named.map(group => (
            <button
              key={group.id}
              type="button"
              className="chip"
              aria-pressed={picked.includes(group.id)}
              onClick={() => toggle(group.id)}
            >
              {group.name}
            </button>
          ))}
        </div>
        <button type="button" className="primary" onClick={() => onChoose(picked)}>
          {texts.net.done}
        </button>
      </section>
    </>
  );
}
