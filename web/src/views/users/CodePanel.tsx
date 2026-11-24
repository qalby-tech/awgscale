"use client";

/** The side panel of the list: a user's connection code and check, or an invitation to a group. */
import { useEffect, useRef, type RefObject } from "react";

import { ddmm } from "@/lib/format";
import { CodeTab, DiagState, type Client } from "@/lib/types";
import {
  closeCode,
  closeInvite,
  dropInvite,
  INVITE_DAYS,
  INVITE_USES,
  makeInvite,
  runDiag,
  setInviteChoice,
  showCodeTab,
  type CodeView,
  type Diag,
  type Invite,
  type InviteView,
} from "@/store/users";
import { Dot, DotKind, IconButton } from "@/ui/controls";
import { Icon, IconName } from "@/ui/Icon";
import { copyText } from "@/ui/toast";

import { QrCode } from "./QrCode";

const TABS: ReadonlyArray<readonly [CodeTab, string]> = [
  [CodeTab.Vpn, "AmneziaVPN"],
  [CodeTab.Wg, "AmneziaWG"],
  [CodeTab.Diag, "Проверка"],
];
const APP_NAMES: Record<CodeTab, string> = Object.fromEntries(TABS) as Record<CodeTab, string>;
const DIAG_DOTS: Record<DiagState, DotKind> = {
  [DiagState.Ok]: DotKind.Good,
  [DiagState.Warn]: DotKind.Off,
  [DiagState.Bad]: DotKind.Crit,
};
/** A panel that opened below the fold (a narrow screen) is brought into view. */
const VISIBLE_MARGIN = 120;

function downloadText(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const anchor = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** What a check found: its lines, or why there are none. */
function DiagResult({ diag }: { diag: Diag | null }) {
  if (!diag || diag.pending) return <div className="muted">Проверяю…</div>;
  if (diag.error) return <div className="err">{diag.error}</div>;
  return (
    <>
      {diag.items.map((item, index) => (
        <div key={index} className={"diag-row " + item.state}>
          <Dot kind={DIAG_DOTS[item.state] ?? DotKind.Crit} />
          <span>{item.text}</span>
        </div>
      ))}
    </>
  );
}

function DiagView({ name, diag }: { name: string; diag: Diag | null }) {
  const waiting = !diag || diag.pending;
  return (
    <>
      <div className="diag" role="status">
        <DiagResult diag={diag} />
      </div>
      <div className="row">
        <button type="button" className="btn" disabled={waiting} onClick={() => void runDiag(name)}>
          Ещё раз
        </button>
      </div>
    </>
  );
}

function CodeBody({ code }: { code: CodeView }) {
  const uriField = useRef<HTMLInputElement>(null);
  const data = code.data;
  const png = data && (code.tab === CodeTab.Vpn ? data.uri_png : data.conf_png);
  return (
    <>
      {png ? (
        <QrCode key={code.tab} png={png} app={APP_NAMES[code.tab]} />
      ) : (
        <div className="qr-ph">
          <Icon name={IconName.Qr} size={32} stroke={1.5} />
          <span>{code.error || (data ? "QR-кода нет" : "Загрузка…")}</span>
        </div>
      )}
      {code.tab === CodeTab.Vpn ? (
        <>
          <input
            id="codeUri"
            ref={uriField}
            className="input"
            type="text"
            readOnly
            value={data?.uri ?? ""}
            aria-label="Строка подключения"
            onFocus={event => event.currentTarget.select()}
          />
          <div className="row">
            <button
              type="button"
              className="btn primary"
              disabled={!data?.uri}
              onClick={() => data?.uri && void copyText(data.uri, uriField.current)}
            >
              <Icon name={IconName.Copy} />
              <span>Скопировать</span>
            </button>
          </div>
        </>
      ) : (
        <div className="row">
          <button
            type="button"
            className="btn primary"
            disabled={!data?.conf}
            onClick={() => data?.conf && downloadText(code.name + ".conf", data.conf)}
          >
            <Icon name={IconName.Down} />
            <span>Скачать .conf</span>
          </button>
        </div>
      )}
    </>
  );
}

function UserCode({ code, user }: { code: CodeView; user: Client | undefined }) {
  return (
    <>
      <div className="panel-head">
        <div className="panel-title">
          <h2>{code.name}</h2>
          <span className="mono ink2" id="codeIp">
            {user?.ip ?? ""}
          </span>
        </div>
        <IconButton icon={IconName.X} label="Закрыть" onClick={closeCode} />
      </div>
      <div className="seg" role="group" aria-label="Приложение">
        {TABS.map(([tab, text]) => (
          <button key={tab} type="button" aria-pressed={code.tab === tab} onClick={() => showCodeTab(tab)}>
            {text}
          </button>
        ))}
      </div>
      {code.tab === CodeTab.Diag ? <DiagView name={code.name} diag={code.diag} /> : <CodeBody code={code} />}
    </>
  );
}

function Pick({
  id,
  label,
  value,
  options,
  onPick,
}: {
  id: string;
  label: string;
  value: number;
  options: ReadonlyArray<readonly [number, string]>;
  onPick: (value: number) => void;
}) {
  return (
    <label className="inv-pick">
      <span>{label}</span>
      <select id={id} className="select" value={value} onChange={event => onPick(parseInt(event.target.value, 10))}>
        {options.map(([count, text]) => (
          <option key={count} value={count}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

const TELEGRAM_TEXT = "Приглашение в VPN: откройте ссылку и следуйте шагам";

/** A fresh link, ready to be copied or sent. */
interface InviteLinkProps {
  link: string;
  png: string | null;
  fieldRef: RefObject<HTMLInputElement | null>;
}

/** The link to copy or send, and its QR code for a phone's camera (the awgscale app reads it too). */
function InviteLink({ link, png, fieldRef }: InviteLinkProps) {
  const shareUrl =
    "https://t.me/share/url?url=" + encodeURIComponent(link) + "&text=" + encodeURIComponent(TELEGRAM_TEXT);
  return (
    <>
      {png ? <QrCode png={png} app="приглашения" /> : null}
      <input
        id="invLink"
        ref={fieldRef}
        className="input"
        type="text"
        readOnly
        value={link}
        aria-label="Ссылка-приглашение"
        onFocus={event => event.currentTarget.select()}
      />
      <div className="row">
        <button type="button" className="btn" onClick={() => void copyText(link, fieldRef.current)}>
          <Icon name={IconName.Copy} />
          <span>Скопировать</span>
        </button>
        <a className="btn" id="invTg" target="_blank" rel="noopener noreferrer" href={shareUrl}>
          Отправить в Telegram
        </a>
      </div>
    </>
  );
}

/** What a line of the list says about an invitation: how much of it is left, until when, who used it. */
function issuedText(issued: Invite, isShownLink: boolean): string {
  const which = isShownLink ? "эта ссылка · " : "";
  const usedBy = issued.used?.length ? " · " + issued.used.join(", ") : "";
  return which + "осталось " + issued.left + " из " + issued.total + " · до " + ddmm(issued.expires_at) + usedBy;
}

interface IssuedListProps {
  list: Invite[];
  shownId: string | null;
  busy: boolean;
  onDrop: (id: string) => void;
}

/** The invitations of this group that still work. */
function IssuedList({ list, shownId, busy, onDrop }: IssuedListProps) {
  return (
    <div className="inv-list" aria-label="Действующие приглашения">
      {list.map(issued => (
        <div key={issued.id} className="inv-row">
          <span>{issuedText(issued, issued.id === shownId)}</span>
          <button
            type="button"
            className="btn small"
            disabled={busy}
            aria-label={"Отозвать приглашение до " + ddmm(issued.expires_at)}
            onClick={() => onDrop(issued.id)}
          >
            Отозвать
          </button>
        </div>
      ))}
    </div>
  );
}

function InviteBody({ invite }: { invite: InviteView }) {
  const linkField = useRef<HTMLInputElement>(null);
  const makeButton = useRef<HTMLButtonElement>(null);
  // a fresh link is ready to be copied; after a revocation the cursor is back on «Создать ссылку»
  useEffect(() => {
    if (invite.link) {
      linkField.current?.focus();
      linkField.current?.select();
    }
  }, [invite.link]);
  const drop = async (id: string) => {
    await dropInvite(id);
    makeButton.current?.focus({ preventScroll: true });
  };
  return (
    <>
      <div className="panel-head">
        <div className="panel-title">
          <h2>Приглашение</h2>
          <span className="ink2">{invite.groupName}</span>
        </div>
        <IconButton icon={IconName.X} label="Закрыть" onClick={closeInvite} />
      </div>
      <div className="row">
        <Pick
          id="invUses"
          label="Устройств"
          value={invite.uses}
          options={INVITE_USES.map(count => [count, String(count)] as const)}
          onPick={uses => setInviteChoice({ uses })}
        />
        <Pick
          id="invDays"
          label="Действует"
          value={invite.days}
          options={INVITE_DAYS}
          onPick={days => setInviteChoice({ days })}
        />
      </div>
      <div className="row">
        <button
          type="button"
          ref={makeButton}
          className="btn primary"
          id="invMake"
          disabled={invite.busy}
          onClick={() => void makeInvite()}
        >
          Создать ссылку
        </button>
      </div>
      {invite.link ? <InviteLink link={invite.link} png={invite.linkPng} fieldRef={linkField} /> : null}
      {invite.list?.length ? (
        <IssuedList list={invite.list} shownId={invite.linkId} busy={invite.busy} onDrop={id => void drop(id)} />
      ) : null}
    </>
  );
}

interface CodePanelProps {
  code: CodeView | null;
  invite: InviteView | null;
  user: Client | undefined;
}

/** Names what the panel shows now: when it changes, the panel is scrolled into view. */
function shownKey(code: CodeView | null, invite: InviteView | null): string {
  if (invite) return "invite:" + (invite.groupId ?? "");
  if (code) return "code:" + code.name + ":" + (code.tab === CodeTab.Diag);
  return "";
}

export function CodePanel({ code, invite, user }: CodePanelProps) {
  const panel = useRef<HTMLElement>(null);
  const shown = shownKey(code, invite);
  useEffect(() => {
    const element = panel.current;
    if (shown && element && element.getBoundingClientRect().top > innerHeight - VISIBLE_MARGIN)
      element.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [shown]);
  return (
    <aside ref={panel} className="card panel" id="codePanel" aria-label="Код подключения" hidden={!invite && !code}>
      {invite ? <InviteBody invite={invite} /> : null}
      {!invite && code ? <UserCode code={code} user={user} /> : null}
    </aside>
  );
}
