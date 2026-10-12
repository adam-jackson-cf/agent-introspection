import { useRef, useState, type KeyboardEvent } from "react";
import { useWidth } from "./charts";

/** One interval row: a point `value` with its `lo`–`hi` whisker over `n` samples. */
export type IntervalRow = {
  key: string;
  label: string;
  n: number;
  /** null reads "not applicable": the group cannot produce the measure. */
  value: number | null;
  lo: number | null;
  hi: number | null;
};
export type IntervalGroup = {
  key: string;
  label: string;
  color: string;
  rows: IntervalRow[];
};
/** The value axis: `fixed` spans min–max with 0/50/100-style gridlines, `log` uses decade ticks. */
export type IntervalAxis = {
  min: number;
  max: number;
  log?: boolean;
  fixed?: boolean;
};

const ROW = 24;
const HEADER = 22;
const LABEL_WIDTH = 132;
const VALUE_WIDTH = 104;
const TOP = 6;
const AXIS_HEIGHT = 22;

/** Powers of ten inside a log axis, ascending. */
export function logTicks(min: number, max: number): number[] {
  const ticks: number[] = [];
  for (let power = Math.ceil(Math.log10(min)); 10 ** power <= max; power += 1)
    ticks.push(10 ** power);
  return ticks;
}

/** The decade range holding every value, for a log axis; values must be positive. */
export function logExtent(values: number[]): { min: number; max: number } {
  const positive = values.filter((value) => value > 0);
  if (positive.length === 0) return { min: 1, max: 10 };
  const low = Math.floor(Math.log10(Math.min(...positive)));
  const high = Math.ceil(Math.log10(Math.max(...positive)));
  return { min: 10 ** low, max: 10 ** Math.max(high, low + 1) };
}

/** Gridline values: decades on a log axis, else the axis ends and midpoint. */
export const axisTicks = (axis: IntervalAxis): number[] =>
  axis.log
    ? logTicks(axis.min, axis.max)
    : [axis.min, (axis.min + axis.max) / 2, axis.max];

/** Position of a value along `[left, left + span]`, clamped to the axis. */
export function scaleValue(
  axis: IntervalAxis,
  value: number,
  left: number,
  span: number,
): number {
  const clamped = Math.min(axis.max, Math.max(axis.min, value));
  const fraction = axis.log
    ? (Math.log10(clamped) - Math.log10(axis.min)) /
      (Math.log10(axis.max) - Math.log10(axis.min))
    : (clamped - axis.min) / (axis.max - axis.min);
  return left + fraction * span;
}

export type Slot = { group: IntervalGroup; row: IntervalRow; y: number };

/** Row positions below each group header, top to bottom. */
function layout(groups: IntervalGroup[]): {
  slots: Slot[];
  headers: { group: IntervalGroup; y: number }[];
  height: number;
} {
  const slots: Slot[] = [];
  const headers: { group: IntervalGroup; y: number }[] = [];
  let y = TOP;
  for (const group of groups) {
    headers.push({ group, y });
    y += HEADER;
    for (const row of group.rows) {
      slots.push({ group, row, y });
      y += ROW;
    }
  }
  return { slots, headers, height: y + AXIS_HEIGHT };
}

const readout = (slot: Slot, format: (value: number) => string): string => {
  const { group, row } = slot;
  const head = `${group.label} ${row.label}, n ${row.n.toLocaleString("en")}`;
  if (row.value === null) return `${head}: not applicable`;
  const range =
    row.lo !== null && row.hi !== null
      ? ` (${format(row.lo)} to ${format(row.hi)})`
      : "";
  return `${head}: ${format(row.value)}${range}`;
};

export function IntervalMarks(props: {
  slot: Slot;
  axis: IntervalAxis;
  left: number;
  span: number;
  minN: number;
  format: (value: number) => string;
}) {
  const { slot, axis, left, span, minN, format } = props;
  const { group, row, y } = slot;
  const mid = y + ROW / 2;
  const x = (value: number) => scaleValue(axis, value, left, span);
  return (
    <g>
      <text x={0} y={mid + 4}>
        {row.label} · n={row.n.toLocaleString("en")}
      </text>
      {row.value !== null && row.lo !== null && row.hi !== null && (
        <line
          className="interval-whisker"
          stroke={group.color}
          x1={x(row.lo)}
          x2={x(row.hi)}
          y1={mid}
          y2={mid}
        />
      )}
      {row.value !== null && (
        <circle
          className="interval-dot"
          cx={x(row.value)}
          cy={mid}
          r={5}
          stroke={group.color}
          fill={row.n < minN ? "var(--color-surface)" : group.color}
        />
      )}
      <text className="interval-value" x={left + span + 10} y={mid + 4}>
        {row.value === null ? "not applicable" : format(row.value)}
      </text>
    </g>
  );
}

/**
 * Whole-window intervals, one row per (group, row): a whisker from `lo` to `hi` and a
 * dot at `value`, hollow when the row has fewer than `minN` samples. One shared axis.
 * The chart is one tab stop: arrows move between rows, Escape clears, and the focused
 * row is read out in a live region.
 */
export function IntervalChart(props: {
  groups: IntervalGroup[];
  axis: IntervalAxis;
  format: (value: number) => string;
  /** Rows with fewer samples than this get a hollow dot. */
  minN: number;
  label: string;
}) {
  const { groups, axis, format, minN, label } = props;
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const [active, setActive] = useState<number | null>(null);
  const { slots, headers, height } = layout(groups);
  if (slots.length === 0)
    return (
      <div className="chart">
        <p className="empty">No rows for the selected range.</p>
      </div>
    );
  const left = LABEL_WIDTH;
  const span = Math.max(0, width - LABEL_WIDTH - VALUE_WIDTH);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const last = slots.length - 1;
    const moves: Record<string, number> = {
      ArrowUp: Math.max(0, (active ?? 0) - 1),
      ArrowLeft: Math.max(0, (active ?? 0) - 1),
      ArrowDown: active === null ? 0 : Math.min(last, active + 1),
      ArrowRight: active === null ? 0 : Math.min(last, active + 1),
      Home: 0,
      End: last,
    };
    if (event.key === "Escape") setActive(null);
    else if (event.key in moves) {
      event.preventDefault();
      setActive(moves[event.key]!);
    }
  };
  const focused = active === null ? undefined : slots[active];
  return (
    <div
      className="chart interval-chart"
      ref={ref}
      role="group"
      aria-label={`${label} interval chart`}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onFocus={() => setActive((current) => current ?? 0)}
      onBlur={() => setActive(null)}
    >
      {width > 0 && (
        <svg
          width={width}
          height={height}
          aria-hidden="true"
          onMouseLeave={() => setActive(null)}
        >
          {axisTicks(axis).map((tick) => (
            <g key={tick}>
              <line
                className={tick === axis.min ? "baseline" : "gridline"}
                x1={scaleValue(axis, tick, left, span)}
                x2={scaleValue(axis, tick, left, span)}
                y1={TOP}
                y2={height - AXIS_HEIGHT}
              />
              <text
                x={scaleValue(axis, tick, left, span)}
                y={height - 6}
                textAnchor="middle"
              >
                {format(tick)}
              </text>
            </g>
          ))}
          {headers.map(({ group, y }) => (
            <g key={group.key}>
              <rect
                x={0}
                y={y + 6}
                width={8}
                height={8}
                rx={2}
                fill={group.color}
              />
              <text className="interval-group" x={14} y={y + 14}>
                {group.label}
              </text>
            </g>
          ))}
          {slots.map((slot, index) => (
            <g
              key={`${slot.group.key}:${slot.row.key}`}
              onMouseEnter={() => setActive(index)}
            >
              {active === index && (
                <rect
                  className="interval-focus"
                  x={0}
                  y={slot.y}
                  width={width}
                  height={ROW}
                />
              )}
              <IntervalMarks
                slot={slot}
                axis={axis}
                left={left}
                span={span}
                minN={minN}
                format={format}
              />
            </g>
          ))}
        </svg>
      )}
      <p className="sr-only" aria-live="polite">
        {focused ? readout(focused, format) : ""}
      </p>
    </div>
  );
}
