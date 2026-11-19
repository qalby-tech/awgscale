import { memo } from "react";

import { fmtBytes } from "@/lib/format";
import type { Traffic } from "@/lib/types";
import { trafficByDay } from "@/store/traffic";

/** "2026-11-07" → "07.11". */
const dayMonth = (isoDay: string): string => isoDay.slice(8, 10) + "." + isoDay.slice(5, 7);

/**
 * One bar per day of the history: the tallest is the full height, today is the
 * last and stands out. Nothing at all when there was not a byte in the 30 days
 * (the rows above already say 0). Redrawn only when the history or the users
 * change, so a pointer resting on a bar keeps its tooltip.
 */
export const TrafficBars = memo(
  function TrafficBars({ data, names }: { data: Traffic | null; names: string[] }) {
    if (!data) return null;
    const days = data.days;
    const values = trafficByDay(data, names);
    const max = Math.max(0, ...values);
    const last = values.length - 1;
    if (!max) return null;
    const label =
      "Трафик по дням за 30 дней: всего " +
      fmtBytes(values.reduce((sum, value) => sum + value, 0)) +
      ", больше всего " +
      dayMonth(days[values.indexOf(max)]) +
      " — " +
      fmtBytes(max) +
      ", сегодня " +
      fmtBytes(values[last]);
    return (
      <>
        <div className="traf-bars" role="img" aria-label={label}>
          {values.map((value, index) => (
            <i
              key={days[index]}
              className={[value ? "on" : "", index === last ? "now" : ""].filter(Boolean).join(" ")}
              title={dayMonth(days[index]) + " — " + fmtBytes(value)}
              style={{ height: (100 * value) / max + "%" }}
            />
          ))}
        </div>
        {/* the scale, for where there is no tooltip (touch) */}
        <div className="traf-x" aria-hidden="true">
          <span>{dayMonth(days[0])}</span>
          <span>{"макс. " + fmtBytes(max)}</span>
          <span>сегодня</span>
        </div>
      </>
    );
  },
  (previous, next) => previous.data === next.data && previous.names.join("\n") === next.names.join("\n"),
);
