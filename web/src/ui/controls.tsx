/** Small controls used all over the page. */
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

import { Icon, IconName } from "./Icon";

/** Icons that read better drawn heavier inside a button. */
const BUTTON_STROKE: Partial<Record<IconName, number>> = { [IconName.More]: 2.4, [IconName.Plus]: 2, [IconName.X]: 2 };

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "title" | "aria-label"> {
  icon: IconName;
  /** Said to screen readers and shown as the tooltip. */
  label: string;
  ref?: Ref<HTMLButtonElement>;
}

export function IconButton({ icon, label, className, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      className={"icon-btn" + (className ? " " + className : "")}
      aria-label={label}
      title={label}
    >
      <Icon name={icon} stroke={BUTTON_STROKE[icon]} />
    </button>
  );
}

interface SwitchProps {
  on: boolean;
  label: string;
  onToggle: () => void;
  disabled?: boolean;
}

export function Switch({ on, label, onToggle, disabled }: SwitchProps) {
  return (
    <button
      type="button"
      className="switch"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={onToggle}
    >
      <i />
    </button>
  );
}

/** A label that only screen readers get. */
export function HiddenLabel({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return (
    <label className="vh" htmlFor={htmlFor}>
      {children}
    </label>
  );
}

/** A coloured dot: fine, idle, or wrong. */
export enum DotKind {
  Good = "",
  Off = " off",
  Crit = " crit",
}
export const Dot = ({ kind = DotKind.Good }: { kind?: DotKind }) => <i className={"dot" + kind} />;
