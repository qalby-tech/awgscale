/**
 * Short messages at the bottom of the page. They are put into the page by
 * hand, not through React: a message lives a few seconds on its own clock and
 * belongs to no section, and whatever removes one early (a script, an
 * extension) must not break the page under it.
 */
import { isAuthError } from "@/lib/api";

export enum ToastKind {
  Info = "info",
  Error = "err",
}

/** The element the messages are put into (ui/Toasts.tsx draws it). */
export const TOASTS_ID = "toasts";

const SHOWN_MS: Record<ToastKind, number> = { [ToastKind.Info]: 2600, [ToastKind.Error]: 6000 };

export function toast(text: string, kind: ToastKind = ToastKind.Info): void {
  const box = document.getElementById(TOASTS_ID);
  if (!box) return;
  const element = document.createElement("div");
  element.className = "toast" + (kind === ToastKind.Error ? " err" : "");
  element.setAttribute("role", kind === ToastKind.Error ? "alert" : "status");
  element.textContent = text;
  box.append(element);
  setTimeout(() => element.remove(), SHOWN_MS[kind]);
}

export const toastError = (text: string): void => toast(text, ToastKind.Error);

/** Says what went wrong — unless the session is gone: then the login form says it. */
export function fail(error: unknown): void {
  if (error && !isAuthError(error)) toastError((error instanceof Error && error.message) || "ошибка");
}

export async function copyText(text: string, field?: HTMLInputElement | null): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    if (!field) return toastError("не удалось скопировать");
    field.focus();
    field.select();
    try {
      document.execCommand("copy");
    } catch {
      return toastError("не удалось скопировать");
    }
  }
  toast("Скопировано");
}
