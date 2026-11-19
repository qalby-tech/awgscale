"use client";

/** Download and upload over the last minutes, with a crosshair under the pointer. */
import { useState, type CSSProperties, type MouseEvent } from "react";

import { fmtBps, num } from "@/lib/format";
import type { HistoryPoint } from "@/lib/types";

const WIDTH = 600;
const HEIGHT = 300;
const COLOR = { down: "#2a78d6", up: "#eb6834", zero: "#b9c0ca", grid: "#e4e7ec" } as const;
/** Round steps of the scale, Mbit/s; the first that gives no more than three lines above zero is used. */
const STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000] as const;
const MAX_LINES = 3;
const MBIT = 1e6;
/** Past this share of the width the tooltip stands to the left of the crosshair. */
const TIP_FLIP = 0.6;
const TIP_GAP = 2;

interface Hover {
  x: number;
  text: string;
}

const minutes = (seconds: number): number => Math.max(1, Math.round(seconds / 60));

function Curve({ points, color }: { points: string; color: string }) {
  return <polyline points={points} stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />;
}

/** Where the tip stands beside the crosshair: to its right, or to its left near the right edge. `x` is 0…1 across the chart. */
function tipPlace(x: number): CSSProperties {
  if (x > TIP_FLIP) return { right: 100 * (1 - x) + TIP_GAP + "%" };
  return { left: 100 * x + TIP_GAP + "%" };
}

/** Rounding slack: a top that is a whole number of steps must not gain a line from floating point. */
const EPSILON = 1e-9;

interface Scale {
  /** The values of the horizontal lines, Mbit/s, zero first. */
  lines: number[];
  /** The top of the chart, Mbit/s. */
  max: number;
  step: number;
}

/** The vertical scale for this history: round steps, no more than a few lines above zero. */
function scaleOf(history: HistoryPoint[]): Scale {
  const top = Math.max(...history.map(point => Math.max(point.down, point.up))) / MBIT;
  const step = STEPS.find(candidate => top / candidate <= MAX_LINES) ?? STEPS[STEPS.length - 1];
  const max = Math.max(step, Math.ceil(top / step - EPSILON) * step);
  const lines: number[] = [];
  for (let value = 0; value <= max + EPSILON; value += step) lines.push(value);
  return { lines, max, step };
}

/** The point of the history nearest to a moment. */
function nearestTo(history: HistoryPoint[], time: number): HistoryPoint {
  let nearest = history[0];
  for (const point of history) {
    if (Math.abs(point.t - time) < Math.abs(nearest.t - time)) nearest = point;
  }
  return nearest;
}

function tipText(point: HistoryPoint): string {
  const clock = new Date(point.t * 1000).toLocaleTimeString("ru-RU");
  return clock + "\n↓ " + fmtBps(point.down) + "\n↑ " + fmtBps(point.up);
}

function Legend() {
  return (
    <div className="legend">
      <span>
        <i style={{ background: COLOR.down }} />
        Скачивание
      </span>
      <span>
        <i style={{ background: COLOR.up }} />
        Отдача
      </span>
    </div>
  );
}

interface PlotProps {
  history: HistoryPoint[];
  scale: Scale;
  startTime: number;
  span: number;
  hover: Hover | null;
}

/** The drawing itself: the lines of the scale with their figures, both curves, the crosshair and its tip. */
function Plot({ history, scale, startTime, span, hover }: PlotProps) {
  const toX = (time: number) => (WIDTH * (time - startTime)) / span;
  const toY = (bps: number) => HEIGHT * (1 - bps / MBIT / scale.max);
  const curve = (pick: (point: HistoryPoint) => number) =>
    history.map(point => toX(point.t).toFixed(1) + "," + toY(pick(point)).toFixed(1)).join(" ");
  /** A line is drawn half a pixel inside the edges, so the zero line and the top one are not cut. */
  const lineY = (value: number) => (value === 0 ? HEIGHT - 0.5 : Math.max(0.5, toY(value * MBIT)));
  return (
    <>
      <svg
        viewBox={"0 0 " + WIDTH + " " + HEIGHT}
        preserveAspectRatio="none"
        role="img"
        aria-label={"Трафик за последние " + minutes(span) + " мин"}
      >
        {scale.lines.map(value => (
          <path
            key={value}
            d={"M 0 " + lineY(value) + " H " + WIDTH}
            stroke={value === 0 ? COLOR.zero : COLOR.grid}
            strokeWidth={1}
          />
        ))}
        <Curve points={curve(point => point.up)} color={COLOR.up} />
        <Curve points={curve(point => point.down)} color={COLOR.down} />
      </svg>
      {scale.lines.map(value => (
        <div key={value} className="yl" style={{ top: 100 * (1 - value / scale.max) + "%" }}>
          {num(value, scale.step < 1 ? 1 : 0)}
        </div>
      ))}
      <div className="chart-cross" hidden={!hover} style={hover ? { left: 100 * hover.x + "%" } : undefined} />
      <div className="chart-tip" hidden={!hover} style={hover ? tipPlace(hover.x) : undefined}>
        {hover?.text}
      </div>
    </>
  );
}

export function TrafficChart({ history }: { history: HistoryPoint[] }) {
  const [hover, setHover] = useState<Hover | null>(null);
  const enough = history.length >= 2;
  const startTime = enough ? history[0].t : 0;
  const span = enough ? Math.max(1, history[history.length - 1].t - startTime) : 1;

  const onMove = (event: MouseEvent<HTMLDivElement>) => {
    if (!enough) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const point = nearestTo(history, startTime + span * fraction);
    setHover({ x: (point.t - startTime) / span, text: tipText(point) });
  };

  return (
    <section className="card pad chart-card" aria-label="Трафик">
      <div className="chart-head">
        <h2 className="card-title">Трафик, Мбит/с</h2>
        <Legend />
      </div>
      <div className="chart" id="chart" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {enough ? (
          <Plot history={history} scale={scaleOf(history)} startTime={startTime} span={span} hover={hover} />
        ) : (
          <div className="chart-empty">Данные накапливаются</div>
        )}
      </div>
      <div className="chart-x">
        <span id="chartFrom">{enough ? minutes(span) + " мин назад" : ""}</span>
        <span>сейчас</span>
      </div>
    </section>
  );
}
