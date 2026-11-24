// What the person gave the join screen (typed, pasted, opened from a file or read from a QR code): an
// awgscale invitation, or a connection given by hand as the AmneziaVPN app takes it. Only the shape is
// looked at here; the Rust side reads it properly (app/core: join.rs, manual.rs) and says what is wrong.

export enum Given {
  // awgscale://join?h=…&c=… or the panel's https://<domain>/i/<code>
  Invitation = "invitation",
  // an Amnezia key vpn://…, the AmneziaVPN app's QR frame of one, or an AmneziaWG config
  Connection = "connection",
  Nothing = "nothing",
}

const INVITATION_PREFIXES = ["awgscale://", "https://"];
const KEY_PREFIX = "vpn://";
const CONFIG_SECTION = "[interface]";
// The AmneziaVPN app's QR frame: URL-safe base64 of bytes that begin with the magic 1984 (0x07C0)
const AMNEZIA_FRAME = /^B8[A-Za-z0-9_-]+={0,2}$/;

export function givenKind(text: string): Given {
  const trimmed = text.trim();
  const lower = trimmed.toLowerCase();
  if (INVITATION_PREFIXES.some(prefix => lower.startsWith(prefix))) {
    return Given.Invitation;
  }
  if (lower.startsWith(KEY_PREFIX) || lower.includes(CONFIG_SECTION) || AMNEZIA_FRAME.test(trimmed)) {
    return Given.Connection;
  }
  return Given.Nothing;
}
