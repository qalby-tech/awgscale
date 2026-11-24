// First run: the link the owner (or the server's install) showed, then a name for this device. Or a connection
// given by hand, as the AmneziaVPN app takes it (a vpn:// key, its QR code, an AmneziaWG config): it becomes the
// device's network at once. On a phone the camera can read either from a QR code.
import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from "react";

import { backend, shownError } from "../backend";
import { Given, givenKind } from "../given";
import { useTexts } from "../i18n";
import { cameraAllowed, onBack, readCode, stopReading } from "../scanner";
import { type InvitationInfo, type ProfileShown } from "../types";
import { Logo } from "./icons";

const DEVICE_NAME_PATTERN = "[A-Za-z0-9_][A-Za-z0-9_\\-]{0,31}";
// a config is a few hundred bytes; anything much larger is not one. Any file may be chosen: a phone's picker
// knows no type for .conf and would grey such files out
const FILE_MAX_BYTES = 64 * 1024;
const SCANNING_CLASS = "scanning";

interface StartProps {
  // a link the phone opened the app with, to put in the field
  opened: string | null;
  onOpenedTaken: () => void;
  onJoined: (profile: ProfileShown) => void;
}

interface ScanningProps {
  onCancel: () => void;
}

// What is drawn over the camera's picture while it reads: a frame to aim with, and the way back.
function Scanning({ onCancel }: ScanningProps) {
  const texts = useTexts();
  useEffect(() => {
    document.documentElement.classList.add(SCANNING_CLASS);
    return () => document.documentElement.classList.remove(SCANNING_CLASS);
  }, []);
  return (
    <main className="scanner">
      <div className="viewfinder" aria-hidden="true" />
      <p>{texts.scanHint}</p>
      <button className="primary" type="button" onClick={onCancel}>
        {texts.cancel}
      </button>
    </main>
  );
}

export function Start({ opened, onOpenedTaken, onJoined }: StartProps) {
  const texts = useTexts();
  const [link, setLink] = useState("");
  const [info, setInfo] = useState<InvitationInfo | null>(null);
  const [device, setDevice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [canScan, setCanScan] = useState(false);
  const [scanning, setScanning] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => {
    backend
      .canScan()
      .then(setCanScan)
      .catch(() => setCanScan(false));
  }, []);

  // A link the phone opened the app with (awgscale://join?…, Android's intent) takes the field's place; it is
  // looked at only when the person presses Continue, as a pasted one.
  useEffect(() => {
    if (opened != null) {
      setLink(opened);
      setInfo(null);
      setError("");
      onOpenedTaken();
    }
  }, [opened, onOpenedTaken]);

  async function look(event: FormEvent) {
    event.preventDefault();
    await take(link);
  }

  // An invitation is looked at, then the device is named; a connection given by hand is kept at once;
  // anything else is said to be neither (by the Rust side, which reads it properly).
  async function take(given: string) {
    if (givenKind(given) === Given.Invitation) {
      await lookAt(given);
    } else {
      await importFrom(given);
    }
  }

  async function lookAt(given: string) {
    setBusy(true);
    setError("");
    try {
      setInfo(await backend.look(given));
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
    } finally {
      setBusy(false);
    }
  }

  async function importFrom(given: string) {
    setBusy(true);
    setError("");
    try {
      onJoined(await backend.importConnection(given));
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
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

  // The camera reads one QR code: an invitation goes into the field and is looked at, a connection is kept;
  // anything else (a Wi-Fi code, a shop's link) is said to be neither.
  async function scanCode() {
    setError("");
    try {
      if (!(await cameraAllowed())) {
        setError(texts.cameraRefused);
        return;
      }
    } catch (failure) {
      setError(shownError(failure, texts.somethingWrong));
      return;
    }
    setScanning(true);
    const back = await onBack(() => void stopReading().catch(() => undefined)).catch(() => null);
    let read: string | null = null;
    try {
      read = await readCode();
    } catch {
      read = null; // cancelled: back to the screen as it was
    } finally {
      setScanning(false);
      void back?.unregister().catch(() => undefined);
    }
    if (read == null) {
      return;
    }
    const kind = givenKind(read);
    if (kind === Given.Nothing) {
      setError(texts.notConnection);
      return;
    }
    if (kind === Given.Invitation) {
      setLink(read.trim());
    }
    await take(read);
  }

  async function openFile(event: ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0];
    event.target.value = "";
    if (chosen == null) {
      return;
    }
    if (chosen.size > FILE_MAX_BYTES) {
      setError(texts.notConnection);
      return;
    }
    await take(await chosen.text());
  }

  if (scanning) {
    return <Scanning onCancel={() => void stopReading().catch(() => undefined)} />;
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
          <div className="field">
            <textarea
              aria-label={texts.inviteLinkOrKey}
              value={link}
              onChange={event => setLink(event.target.value)}
              rows={2}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              required
            />
          </div>
          <button className="primary" type="submit" disabled={busy || link.trim().length === 0}>
            {busy ? texts.checking : texts.continueButton}
          </button>
          {canScan && (
            <button className="secondary" type="button" onClick={() => void scanCode()} disabled={busy}>
              {texts.scanCode}
            </button>
          )}
          <button className="ghost" type="button" onClick={() => file.current?.click()} disabled={busy}>
            {texts.importFile}
          </button>
          <input ref={file} type="file" hidden onChange={event => void openFile(event)} />
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
