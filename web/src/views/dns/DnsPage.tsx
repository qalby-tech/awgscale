"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { ago, num } from "@/lib/format";
import { useCore } from "@/store/core";
import { Dot, DotKind, HiddenLabel, IconButton } from "@/ui/controls";
import { Icon, IconName } from "@/ui/Icon";
import { menuButton } from "@/ui/menu";
import { PageHead } from "@/ui/PageHead";
import { copyText, fail, toastError } from "@/ui/toast";

import { addDevice, dnsUrl, loadDns, revokeDevice, selectDevice, useDns, type DnsDevice } from "./store";

/** The list is re-read with every sixth answer of the state poll. */
const RELOAD_EVERY_TICKS = 6;
const NAME_RE = /^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}$/;
const NAME_MAX = 32;
const NAME_FIELD = "dnsName";
/** A panel that opened below the fold is scrolled to. */
const FOLD_MARGIN = 120;

function Status({ device }: { device: DnsDevice }) {
  if (!device.last)
    return (
      <span className="st off">
        <Dot kind={DotKind.Off} />
        не подключалось
      </span>
    );
  return (
    <span className={"st" + (device.admitted ? "" : " off")}>
      <Dot kind={device.admitted ? DotKind.Good : DotKind.Off} />
      {ago(device.last, Date.now() / 1000)}
    </span>
  );
}

function DeviceRow({ device, selected, onAddress }: { device: DnsDevice; selected: boolean; onAddress: () => void }) {
  return (
    <tr className={selected ? "sel" : ""}>
      <td className="c-name">
        <div className="name-cell">
          <span className="uname" title={device.name}>
            {device.name}
          </span>
        </div>
        <div className="c-narrow">
          <Status device={device} />
        </div>
      </td>
      <td className="c-wide">
        <Status device={device} />
      </td>
      <td className={"c-wide num" + (device.queries ? "" : " muted")}>{device.queries ? num(device.queries) : "—"}</td>
      <td className="act">
        <div className="act-in">
          <button
            type="button"
            className={"btn" + (selected ? " pressed" : "")}
            aria-pressed={selected}
            data-k={device.name + ":addr"}
            onClick={onAddress}
          >
            Адрес
          </button>
          <IconButton
            icon={IconName.More}
            label={"Действия с устройством " + device.name}
            {...menuButton(() => [{ label: "Отозвать", danger: true, run: () => void revokeDevice(device.name) }])}
          />
        </div>
      </td>
    </tr>
  );
}

function AddForm() {
  const field = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const input = field.current;
    if (!input) return;
    const name = input.value.trim();
    if (!NAME_RE.test(name)) {
      toastError("Имя: латиница, цифры, _ и -, до 32 символов");
      input.focus();
      return;
    }
    setBusy(true);
    try {
      await addDevice(name);
      input.value = "";
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="row create-row" onSubmit={submit}>
      <HiddenLabel htmlFor={NAME_FIELD}>Имя нового устройства</HiddenLabel>
      <input
        id={NAME_FIELD}
        ref={field}
        className="input"
        type="text"
        placeholder="Имя нового устройства"
        autoComplete="off"
        spellCheck={false}
        maxLength={NAME_MAX}
      />
      <button id="dnsAdd" className="btn primary" type="submit" disabled={busy}>
        Добавить
      </button>
    </form>
  );
}

function AddressPanel({ device }: { device: DnsDevice | null }) {
  const box = useRef<HTMLElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const name = device?.name;
  useEffect(() => {
    const panel = box.current;
    if (name && panel && panel.getBoundingClientRect().top > innerHeight - FOLD_MARGIN)
      panel.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [name]);
  const url = device ? dnsUrl(device) : "";
  return (
    <aside className="card panel" id="dnsPanel" aria-label="Адрес DNS" hidden={!device} ref={box}>
      {device ? (
        <>
          <div className="panel-head">
            <div className="panel-title">
              <h2>{device.name}</h2>
            </div>
            <IconButton icon={IconName.X} label="Закрыть" onClick={() => selectDevice(null)} />
          </div>
          <input
            ref={field}
            className="input"
            type="text"
            readOnly
            value={url}
            aria-label="Адрес DNS для устройства"
            onFocus={event => event.target.select()}
          />
          <div className="row">
            <button type="button" className="btn primary" onClick={() => void copyText(url, field.current)}>
              <Icon name={IconName.Copy} />
              <span>Скопировать</span>
            </button>
          </div>
        </>
      ) : null}
    </aside>
  );
}

export function DnsPage() {
  const tick = useCore(state => state.tick);
  const devices = useDns(state => state.devices);
  const selected = useDns(state => state.selected);

  useEffect(() => {
    void loadDns();
  }, []);
  useEffect(() => {
    if (tick % RELOAD_EVERY_TICKS === 0) void loadDns();
  }, [tick]);

  return (
    <>
      <PageHead title="DNS без VPN" />
      <div className="split">
        <section className="card table-card grow" aria-label="Устройства">
          <AddForm />
          <div className="table-wrap">
            <table className="t-dns">
              <thead>
                <tr>
                  <th scope="col">Устройство</th>
                  <th scope="col" className="c-wide">
                    Статус
                  </th>
                  <th scope="col" className="num c-wide">
                    Запросы
                  </th>
                  <th scope="col">
                    <span className="vh">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody id="dnsBody">
                {devices?.map(device => (
                  <DeviceRow
                    key={device.name}
                    device={device}
                    selected={device.name === selected}
                    onAddress={() => selectDevice(device.name === selected ? null : device.name)}
                  />
                ))}
                {devices && !devices.length ? (
                  <tr>
                    <td colSpan={4} className="muted">
                      Устройств пока нет
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
        <AddressPanel device={devices?.find(device => device.name === selected) ?? null} />
      </div>
    </>
  );
}
