// First run: the link the owner (or the server's install) showed, then a name for this device.
import { type FormEvent, useState } from "react";

import { backend, shownError } from "../backend";
import { useTexts } from "../i18n";
import { type InvitationInfo, type ProfileShown } from "../types";
import { Logo } from "./icons";

const DEVICE_NAME_PATTERN = "[A-Za-z0-9_][A-Za-z0-9_\\-]{0,31}";

interface StartProps {
  onJoined: (profile: ProfileShown) => void;
}

export function Start({ onJoined }: StartProps) {
  const texts = useTexts();
  const [link, setLink] = useState("");
  const [info, setInfo] = useState<InvitationInfo | null>(null);
  const [device, setDevice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function look(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setInfo(await backend.look(link));
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    } finally {
      setBusy(false);
    }
  }

  async function join(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onJoined(await backend.join(link, device));
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
      setBusy(false);
    }
  }

  function another() {
    setInfo(null);
    setError("");
  }

  return (
    <main className="screen start">
      <div className="brand">
        <Logo />
        <span>awgscale</span>
      </div>
      <h1>{texts.connectTitle}</h1>
      {info == null ? (
        <form className="stack" onSubmit={look}>
          <label className="field">
            <span>{texts.inviteLink}</span>
            <input
              value={link}
              onChange={event => setLink(event.target.value)}
              placeholder="awgscale://join?…"
              autoComplete="off"
              spellCheck={false}
              required
            />
          </label>
          <button className="primary" type="submit" disabled={busy || link.trim().length === 0}>
            {busy ? texts.checking : texts.continueButton}
          </button>
        </form>
      ) : (
        <form className="stack" onSubmit={join}>
          <p className="note">{texts.invitationLeft(info.left, info.total, info.group)}</p>
          <label className="field">
            <span>{texts.deviceName}</span>
            <input
              value={device}
              onChange={event => setDevice(event.target.value)}
              placeholder="anna_phone"
              pattern={DEVICE_NAME_PATTERN}
              title={texts.deviceNameRule}
              autoComplete="off"
              spellCheck={false}
              required
            />
          </label>
          <button className="primary" type="submit" disabled={busy || device.length === 0}>
            {busy ? texts.joining : texts.join}
          </button>
          <button className="ghost" type="button" onClick={another} disabled={busy}>
            {texts.anotherLink}
          </button>
        </form>
      )}
      {error.length > 0 && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}
