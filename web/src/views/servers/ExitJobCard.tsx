/** The job the exits service is doing, or has just done, step by step. */
import { JobKind, JobState, StepState, type ExitJob } from "@/lib/types";

/** A finished job stays on the page for this long, in seconds. */
const SHOWN_AFTER_FINISH = 900;

const KINDS: Record<JobKind, string> = {
  [JobKind.Add]: "Подключение выхода",
  [JobKind.Update]: "Обновление выхода",
  [JobKind.Clean]: "Уборка на удалённом выходе",
};
const OUTCOMES: Record<JobState, string> = {
  [JobState.Running]: " — идёт",
  [JobState.Done]: " — готово",
  [JobState.Failed]: " — не удалось",
};
const MARKS: Record<StepState, string> = {
  [StepState.Done]: "✓",
  [StepState.Run]: "…",
  [StepState.Fail]: "✕",
  [StepState.Skip]: "–",
  [StepState.Wait]: "·",
};

/** Is the job worth showing now. */
export function jobIsFresh(job: ExitJob | null | undefined, now: number): job is ExitJob {
  return (
    !!job &&
    (job.state === JobState.Running ||
      job.state === JobState.Failed ||
      (!!job.finished && now - job.finished < SHOWN_AFTER_FINISH))
  );
}

export function ExitJobCard({ job }: { job: ExitJob }) {
  return (
    <div className="job">
      <h2 className="card-title">
        {(KINDS[job.kind] ?? "Операция") +
          " " +
          (job.name || job.address) +
          (OUTCOMES[job.state] ?? OUTCOMES[JobState.Failed])}
      </h2>
      <ol>
        {(job.steps ?? []).map((step, index) => (
          <li key={index} className={step.state}>
            <span>{MARKS[step.state] ?? MARKS[StepState.Wait]}</span>
            <span>{step.title}</span>
          </li>
        ))}
      </ol>
      {job.error ? <div className="err">{job.error}</div> : null}
      {job.state === JobState.Failed && job.kind === JobKind.Add ? (
        <div className="hint">
          Сделанные шаги не пропали: исправьте причину и подключите сервер ещё раз с тем же адресом и именем.
        </div>
      ) : null}
      {job.state === JobState.Done && job.reboot_needed ? (
        <div className="hint">Новое ядро заработает после перезагрузки: «Обновить и перезагрузить».</div>
      ) : null}
    </div>
  );
}
