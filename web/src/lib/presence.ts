/** Where a user is right now, as one value: every place that shows a user decides from it. */
import type { Client } from "./types";

export enum Presence {
  /** The admin paused the user: the key is kept, nothing passes. */
  Paused = "paused",
  Online = "online",
  /** Not connected now, but has been. */
  Seen = "seen",
  /** Has never connected. */
  Never = "never",
}

export function presenceOf(client: Client): Presence {
  if (client.suspended) return Presence.Paused;
  if (client.online) return Presence.Online;
  return client.handshake ? Presence.Seen : Presence.Never;
}
