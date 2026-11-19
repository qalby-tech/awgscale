"use client";

/** The forms of the site lists: the search, the field that adds an entry to a card, the «list by link» form. */
import { useEffect, useRef, type FormEvent, type KeyboardEvent, type RefObject } from "react";

import { withStar } from "@/lib/entries";

import { HiddenLabel } from "./controls";
import { Icon, IconName } from "./Icon";
import { Key } from "@/lib/dom";

interface SearchBoxProps {
  id: string;
  onSearch: (raw: string) => void;
}

export function SearchBox({ id, onSearch }: SearchBoxProps) {
  return (
    <label className="search">
      <Icon name={IconName.Search} />
      <input
        id={id}
        type="search"
        placeholder="Найти адрес или группу"
        aria-label="Найти адрес или группу"
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        onInput={event => onSearch(event.currentTarget.value)}
      />
    </label>
  );
}

/** Puts a star into the field where it makes sense, and the cursor after it. */
function insertStar(field: HTMLInputElement, onChange: () => void): void {
  const known = typeof field.selectionStart === "number";
  const edit = withStar(field.value, known ? field.selectionStart : null, known ? field.selectionEnd : null);
  if (edit.value !== field.value) {
    field.value = edit.value;
    onChange();
  }
  field.focus();
  try {
    field.setSelectionRange(edit.caret, edit.caret);
  } catch {
    /* not a text field */
  }
}

/** The «Заменить N записей» tick under the field: the entries the typed one makes redundant. */
export interface ReplaceOffer {
  text: string;
  shown: boolean;
  checkRef: RefObject<HTMLInputElement | null>;
}

interface AddRowProps {
  /** The id of the field; the message and the replace row are named after it. */
  id: string;
  allowNet: boolean;
  label: string;
  error: string;
  inputRef: RefObject<HTMLInputElement | null>;
  /** The text changed (typed, or a star was put in). */
  onInput: () => void;
  onAdd: () => void;
  onClose: () => void;
  replace?: ReplaceOffer;
}

/** The field that opens inside a group card when its «+» is pressed. */
export function AddRow({ id, allowNet, label, error, inputRef, onInput, onAdd, onClose, replace }: AddRowProps) {
  useEffect(() => {
    inputRef.current?.focus();
  }, [inputRef]);
  const onKey = (event: KeyboardEvent) => {
    if (event.key === Key.Escape) {
      event.preventDefault();
      onClose();
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onAdd();
  };
  return (
    <div className="addrow">
      <form className="row" onSubmit={submit}>
        <button
          type="button"
          className="icon-btn bordered"
          aria-label="Вставить звёздочку"
          title="Вставить звёздочку"
          onMouseDown={event => event.preventDefault()}
          onClick={() => {
            if (inputRef.current) insertStar(inputRef.current, onInput);
          }}
        >
          <Icon name={IconName.Star} />
        </button>
        <input
          id={id}
          ref={inputRef}
          className="input code"
          type="text"
          placeholder={allowNet ? "Домен или сеть" : "Домен"}
          aria-label={label}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          onInput={onInput}
          onKeyDown={onKey}
        />
        <button className="btn primary" type="submit">
          Добавить
        </button>
      </form>
      <div className="err" id={id + "Err"} role="alert">
        {error}
      </div>
      {replace ? (
        <label className="check" id={id + "ReplaceRow"} hidden={!replace.shown}>
          <input type="checkbox" id={id + "Replace"} ref={replace.checkRef} defaultChecked />
          <span id={id + "ReplaceText"}>{replace.text}</span>
        </label>
      ) : null}
    </div>
  );
}

const LINK_NAME_LENGTH = 40;
const LINK_URL_LENGTH = 500;

export interface LinkFields {
  name: HTMLInputElement;
  url: HTMLInputElement;
}

interface LinkFormProps {
  /** Prefix of the ids of the form's parts. */
  id: string;
  error: string;
  /** The list is being connected right now. */
  busy?: boolean;
  onAdd: (fields: LinkFields) => void;
  onClose: () => void;
}

/** «Список по ссылке»: a name and a link to a list file. */
export function LinkForm({ id, error, busy, onAdd, onClose }: LinkFormProps) {
  const name = useRef<HTMLInputElement>(null);
  const url = useRef<HTMLInputElement>(null);
  useEffect(() => {
    name.current?.focus();
  }, []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (name.current && url.current) onAdd({ name: name.current, url: url.current });
  };
  return (
    <section className="card linkform" aria-label="Подключить список по ссылке">
      <form className="row" onSubmit={submit}>
        <HiddenLabel htmlFor={id + "Name"}>Название</HiddenLabel>
        <input
          id={id + "Name"}
          ref={name}
          className="input name"
          type="text"
          placeholder="Название"
          maxLength={LINK_NAME_LENGTH}
          autoComplete="off"
        />
        <HiddenLabel htmlFor={id + "Url"}>Ссылка</HiddenLabel>
        <input
          id={id + "Url"}
          ref={url}
          className="input code url"
          type="url"
          placeholder="https://"
          maxLength={LINK_URL_LENGTH}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
        />
        <button className="btn primary" type="submit" id={id + "Go"}>
          {busy ? "Подключаю…" : "Подключить"}
        </button>
        <button className="btn" type="button" onClick={onClose}>
          Отмена
        </button>
      </form>
      <div className="err" id={id + "Err"} role="alert">
        {error}
      </div>
    </section>
  );
}

interface LinkButtonProps {
  id: string;
  open: boolean;
  onToggle: () => void;
}

/** The toolbar button that opens and closes the «list by link» form. */
export function LinkButton({ id, open, onToggle }: LinkButtonProps) {
  return (
    <button type="button" className={"btn" + (open ? " pressed" : "")} id={id} aria-expanded={open} onClick={onToggle}>
      <Icon name={IconName.Link} stroke={1.8} />
      <span>Список по ссылке</span>
    </button>
  );
}

export function NewGroupButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="btn" onClick={onClick}>
      <Icon name={IconName.Plus} stroke={2} />
      <span>Новая группа</span>
    </button>
  );
}

/** What a grid says when it has no card to show. */
export const NoCards = ({ text }: { text: string }) => (
  <div className="none" role="status">
    {text}
  </div>
);
