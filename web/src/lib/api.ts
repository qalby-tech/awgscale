/** The one way the page talks to the panel server. */

export class ApiError<D = unknown> extends Error {
  constructor(
    message: string,
    readonly auth = false,
    readonly data: D | null = null,
  ) {
    super(message);
  }
}

export enum Method {
  Get = "GET",
  Post = "POST",
  Delete = "DELETE",
}

export interface ApiOptions {
  method?: Method;
  body?: unknown;
}

const LOGIN_PATH = "/api/login";
const STATE_PATH = "/api/state";

let onUnauthorized: () => void = () => {};
/** What to do when the server says the session is gone (the shell shows the login form). */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

interface Envelope {
  ok?: boolean;
  error?: string;
}

export async function api<T = unknown>(path: string, opts: ApiOptions = {}): Promise<T> {
  const method = opts.method ?? Method.Get;
  const init: RequestInit = { method, credentials: "same-origin" };
  if (method !== Method.Get) {
    init.headers = { "Content-Type": "application/json", "X-Panel": "1" };
    if (method !== Method.Delete) init.body = JSON.stringify(opts.body ?? {});
  }
  let answer: Response;
  try {
    answer = await fetch(path, init);
  } catch {
    throw new ApiError("нет связи с панелью");
  }
  let body: (T & Envelope) | null = null;
  try {
    body = await answer.json();
  } catch {
    body = null;
  } // always read the body, also of an error answer
  if (answer.status === 401 && path !== LOGIN_PATH) {
    onUnauthorized();
    throw new ApiError("нужен вход", true);
  }
  if (body == null) throw new ApiError("сервер ответил непонятно (" + answer.status + ")");
  if (path === STATE_PATH && answer.ok) return body;
  if (!answer.ok || body.ok === false) throw new ApiError(body.error || "ошибка " + answer.status, false, body);
  return body;
}

export const post = <T = unknown>(path: string, body?: unknown) => api<T>(path, { method: Method.Post, body });
export const del = <T = unknown>(path: string) => api<T>(path, { method: Method.Delete });

/** The auth flag of whatever was thrown. */
export const isAuthError = (error: unknown): boolean => error instanceof ApiError && error.auth;
export const errorText = (error: unknown): string => (error instanceof Error && error.message) || "ошибка";
