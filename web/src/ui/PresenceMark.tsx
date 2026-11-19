import { Presence } from "@/lib/presence";

import { Dot, DotKind } from "./controls";
import { Icon, IconName } from "./Icon";

const PAUSE_SIZE = 12;
const PAUSE_STROKE = 2.6;

/** The small sign in front of a user's name: a pause mark, or a dot that is lit while the user is online. */
export function PresenceMark({ presence }: { presence: Presence }) {
  if (presence === Presence.Paused) return <Icon name={IconName.Pause} size={PAUSE_SIZE} stroke={PAUSE_STROKE} />;
  return <Dot kind={presence === Presence.Online ? DotKind.Good : DotKind.Off} />;
}
