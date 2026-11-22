"use client";

/** The pieces a card of a group is made of: its head, its chips, and the small forms that name things. */
import { Fragment, useEffect, useRef, useState, type KeyboardEvent, type ReactNode, type Ref } from "react";

import { IconButton, Switch } from "./controls";
import { Icon, IconName } from "./Icon";
import { menuButton, type MenuItem } from "./menu";
import { Key } from "@/lib/dom";

/** Text with its wildcard stars set off. */
export function StarText({ text }: { text: string }) {
  return (
    <>
      {text.split("*").map((part, index) => (
        <Fragment key={index}>
          {index ? <b className="star">*</b> : null}
          {part}
        </Fragment>
      ))}
    </>
  );
}

interface ChipProps {
  text: string;
  sub?: string;
  className?: string;
  title?: string;
  /** With it the chip can be removed: by its cross, or from the menu under it. */
  onRemove?: () => void;
  removeLabel?: string;
}

const DEFAULT_REMOVE = "Убрать";

export function Chip({ text, sub, className, title, onRemove, removeLabel = DEFAULT_REMOVE }: ChipProps) {
  const label = (
    <>
      <StarText text={text} />
      {sub ? <span className="chip-sub">{sub}</span> : null}
    </>
  );
  const cls = "chip" + (className ? " " + className : "");
  if (!onRemove)
    return (
      <span className={cls + " ro"} data-item={text} title={title}>
        <span className="chip-t">{label}</span>
      </span>
    );
  return (
    <span className={cls} data-item={text} title={title}>
      <button
        type="button"
        className="chip-t"
        {...menuButton(() => [{ label: removeLabel, danger: true, run: onRemove }])}
      >
        {label}
      </button>
      <button
        type="button"
        className="chip-x"
        aria-label={removeLabel + ": " + text}
        title={removeLabel}
        onClick={onRemove}
      >
        <Icon name={IconName.X} size={12} />
      </button>
    </span>
  );
}

/** Draws the eye to a chip that is already there. */
export function flashChip(grid: HTMLElement | null, text: string): void {
  if (!grid) return;
  for (const chip of grid.querySelectorAll<HTMLElement>(".chip")) {
    if (chip.dataset.item !== text) continue;
    chip.classList.remove("flash");
    void chip.offsetWidth;
    chip.classList.add("flash");
    chip.scrollIntoView({ block: "nearest", behavior: "smooth" });
    break;
  }
}

interface RenameInputProps {
  value: string;
  maxLength: number;
  /** The new name, or null when nothing is to change. Called once. */
  onDone: (next: string | null) => void;
}

/** A title turned into a field: Enter or leaving it saves, Escape gives up. */
export function RenameInput({ value, maxLength, onDone }: RenameInputProps) {
  const field = useRef<HTMLInputElement>(null);
  const done = useRef(false);
  useEffect(() => {
    field.current?.focus();
    field.current?.select();
  }, []);
  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    const typed = field.current?.value.trim() ?? "";
    onDone(save && typed && typed !== value ? typed : null);
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === Key.Enter) {
      event.preventDefault();
      finish(true);
    } else if (event.key === Key.Escape) {
      event.stopPropagation();
      finish(false);
    }
  };
  return (
    <input
      ref={field}
      className="input rename"
      type="text"
      defaultValue={value}
      maxLength={maxLength}
      aria-label="Название"
      onKeyDown={onKey}
      onBlur={() => finish(true)}
    />
  );
}

interface NameCardProps {
  placeholder: string;
  maxLength: number;
  onCreate: (name: string) => void;
  onCancel: () => void;
  /** To put the cursor back when «new» is pressed again. */
  inputRef?: Ref<HTMLInputElement>;
}

/** The card that asks for the name of a new group. */
export function NameCard({ placeholder, maxLength, onCreate, onCancel, inputRef }: NameCardProps) {
  const own = useRef<HTMLInputElement | null>(null);
  const setRef = (element: HTMLInputElement | null) => {
    own.current = element;
    if (typeof inputRef === "function") inputRef(element);
    else if (inputRef) inputRef.current = element;
  };
  useEffect(() => {
    own.current?.focus();
  }, []);
  const create = () => {
    const value = own.current?.value.trim();
    if (!value) {
      own.current?.focus();
      return;
    }
    onCreate(value);
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === Key.Enter) {
      event.preventDefault();
      create();
    } else if (event.key === Key.Escape) onCancel();
  };
  return (
    <section className="card group bare new-card">
      <div className="row">
        <input
          ref={setRef}
          className="input"
          type="text"
          placeholder={placeholder}
          maxLength={maxLength}
          aria-label={placeholder}
          onKeyDown={onKey}
        />
        <button type="button" className="btn primary" onClick={create}>
          Создать
        </button>
        <IconButton icon={IconName.X} label="Отмена" onClick={onCancel} />
      </div>
    </section>
  );
}

/** The «+» of a card: either opens a field in the card, or a menu of what can be added. */
export type PlusAction =
  | { label: string; pressed: boolean; onClick: () => void }
  | { label: string; items: () => MenuItem[]; emptyText?: string };

interface GroupHeadProps {
  name: string;
  on: boolean;
  onToggle: () => void;
  disabled?: boolean;
  /** The list comes from a link, not typed by hand. */
  link?: boolean;
  /** A word beside the name: where the group leads, when it is not the usual way. */
  tag?: string | null;
  count?: ReactNode;
  day?: string | null;
  plus?: PlusAction;
  onRefresh?: (() => void) | null;
  onRename?: ((name: string) => void) | null;
  onDelete?: (() => void) | null;
  maxLength?: number;
}

const DEFAULT_NAME_LENGTH = 60;

export function GroupHead(props: GroupHeadProps) {
  const [renaming, setRenaming] = useState(false);
  const items: MenuItem[] = [];
  if (props.onRename) items.push({ label: "Переименовать", run: () => setRenaming(true) });
  if (props.onDelete) items.push({ label: props.link ? "Отключить" : "Удалить", danger: true, run: props.onDelete });
  const finishRenaming = (next: string | null) => {
    setRenaming(false);
    if (next) props.onRename?.(next);
  };
  return (
    <div className="group-head">
      <Switch
        on={props.on}
        label={(props.on ? "Включено: " : "Выключено: ") + props.name}
        onToggle={props.onToggle}
        disabled={props.disabled}
      />
      {props.link ? (
        <span className="group-ic" role="img" aria-label="Список по ссылке" title="Список по ссылке">
          <Icon name={IconName.Link} size={16} stroke={2} />
        </span>
      ) : null}
      {renaming ? (
        <RenameInput value={props.name} maxLength={props.maxLength ?? DEFAULT_NAME_LENGTH} onDone={finishRenaming} />
      ) : (
        <h2 className="group-name" title={props.name}>
          {props.name}
        </h2>
      )}
      {props.tag ? <span className="badge go-direct">{props.tag}</span> : null}
      {props.count != null ? <span className="group-count">{props.count}</span> : null}
      <span className="spacer" />
      {props.day != null ? <span className="group-day">{props.day}</span> : null}
      {props.plus && "items" in props.plus ? (
        <IconButton
          icon={IconName.Plus}
          label={props.plus.label}
          {...menuButton(props.plus.items, props.plus.emptyText)}
        />
      ) : null}
      {props.plus && "onClick" in props.plus ? (
        <IconButton
          icon={IconName.Plus}
          label={props.plus.label}
          className={props.plus.pressed ? "pressed" : ""}
          aria-pressed={props.plus.pressed}
          onClick={props.plus.onClick}
        />
      ) : null}
      {props.onRefresh ? <IconButton icon={IconName.Sync} label="Обновить списки" onClick={props.onRefresh} /> : null}
      {items.length ? (
        <IconButton icon={IconName.More} label={"Действия: " + props.name} {...menuButton(() => items)} />
      ) : null}
    </div>
  );
}
