"use client";

import { useEffect, useRef, useState } from "react";

import { exitsOf, nowOf, useCore } from "@/store/core";
import { jobRunning, loadExits, openExitForm, useExits } from "@/store/exits";
import { PageHead } from "@/ui/PageHead";

import { AddExitButton, ExitForm } from "./ExitForm";
import { ExitJobCard, jobIsFresh } from "./ExitJobCard";
import { ServersTable } from "./ServersTable";
import { SharesEditor } from "./SharesEditor";

/** A job's progress is followed with every poll; without one the registry is re-read now and then. */
const RELOAD_EVERY_TICKS = 5;

export function ServersPage() {
  const state = useCore(state => state.state);
  const tick = useCore(state => state.tick);
  const registry = useExits(state => state.registry);
  const formOpen = useExits(state => state.formOpen);
  const sharesOpen = useExits(state => state.sharesOpen);
  const [opened] = useState(tick);
  const [focusForm, setFocusForm] = useState(false);
  const running = useRef(false);
  running.current = jobRunning(registry);

  useEffect(() => {
    const polls = tick - opened;
    if (!polls || running.current || polls % RELOAD_EVERY_TICKS === 0) void loadExits();
  }, [tick, opened]);

  const job = registry?.job;
  const showJob = jobIsFresh(job, nowOf(state));
  const exits = exitsOf(state);
  const openFormFocused = () => {
    setFocusForm(true);
    openExitForm(true);
  };

  return (
    <>
      <PageHead title="Серверы" />
      <ServersTable state={state} />
      <section className="card pad" id="exitShares" aria-label="Доли выходов" hidden={!sharesOpen}>
        {sharesOpen ? <SharesEditor exits={exits} /> : null}
      </section>
      <section className="card pad" id="exitJob" aria-label="Операция с выходом" hidden={!showJob}>
        {showJob ? <ExitJobCard job={job} /> : null}
      </section>
      <section className="card pad" id="exitForm" aria-label="Новый выход">
        {formOpen ? (
          <ExitForm exitCount={exits.length} focus={focusForm} />
        ) : (
          <AddExitButton onOpen={openFormFocused} />
        )}
      </section>
    </>
  );
}
