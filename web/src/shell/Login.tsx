"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { login } from "@/store/core";
import { BrandMark } from "./BrandMark";

import { PRODUCT } from "./sections";

export function Login({ shown }: { shown: boolean }) {
  const field = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (shown) field.current?.focus();
  }, [shown]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const refused = await login(field.current?.value ?? "");
    if (refused) setError(refused);
    else if (field.current) field.current.value = "";
    setBusy(false);
  };

  return (
    <div id="login" className="login" hidden={!shown}>
      <form id="loginForm" className="card login-card" onSubmit={submit}>
        <div className="brand">
          <div className="brand-mark" id="loginMark">
            <BrandMark />
          </div>
          <div className="brand-name">{PRODUCT}</div>
        </div>
        <label className="field">
          <span>Пароль</span>
          <input id="pw" ref={field} className="input" type="password" autoComplete="current-password" required />
        </label>
        <div id="loginErr" className="err" role="alert">
          {error}
        </div>
        <button id="loginBtn" className="btn primary" type="submit" disabled={busy}>
          Войти
        </button>
      </form>
    </div>
  );
}
