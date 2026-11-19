"use client";

import { useCore } from "@/store/core";

import { TOASTS_ID } from "./toast";

/** The place of the messages (ui/toast.ts adds them), and the one lasting notice: a newer build. */
export function Toasts() {
  const newBuild = useCore(state => state.newBuild);
  return (
    <div id={TOASTS_ID} className="toasts" aria-live="polite">
      {newBuild ? (
        <div className="toast" id="uiNew" role="status">
          <span>Панель обновилась</span>
          <button type="button" className="btn" onClick={() => location.reload()}>
            Обновить
          </button>
        </div>
      ) : null}
    </div>
  );
}
