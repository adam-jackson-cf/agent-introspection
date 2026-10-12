import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import type { Row, Scalar } from "./contracts";
import { HARNESS_LABEL, num } from "./format";

export type Series = { key: string; label: string; color: string };

/** What the enclosing Panel tells its charts and tables. */
export const PanelContext = createContext<{
  label: string;
  harnessChips: boolean;
}>({ label: "", harnessChips: false });
type Format = (value: number | null) => string;

const HEIGHT = 280;
const PAD = { top: 10, right: 8, bottom: 22, left: 64 };

function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(Math.floor(element.getBoundingClientRect().width));
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.floor(entry!.contentRect.width)),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

/** Rounds an axis maximum up to 1, 2, or 5 × 10ⁿ. */
function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].find((candidate) => candidate * power >= value)!;
  return step * power;
}

export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="legend" aria-label="Legend">
      {series.map((entry) => (
        <li key={entry.key}>
          <i style={{ background: entry.color }} aria-hidden="true" />
          {entry.label}
        </li>
      ))}
    </ul>
  );
}

/** Fills every UTC day between the first and last row so gaps stay visible. */
function continuousDays(rows: Row[]): Row[] {
  if (rows.length === 0) return rows;
  const byDay = new Map(rows.map((row) => [String(row.day), row]));
  const days = [...byDay.keys()].sort();
  const out: Row[] = [];
  for (
    let day = Date.parse(`${days[0]}T00:00:00Z`);
    day <= Date.parse(`${days[days.length - 1]}T00:00:00Z`);
    day += 86_400_000
  ) {
    const key = new Date(day).toISOString().slice(0, 10);
    out.push(byDay.get(key) ?? { day: key });
  }
  return out;
}

/** Splits a line into runs of consecutive days with values; missing days break it. */
function runs(
  points: (readonly [number, number | null])[],
): [number, number][][] {
  const result: [number, number][][] = [];
  let current: [number, number][] = [];
  for (const [index, amount] of points) {
    if (amount === null) {
      if (current.length) result.push(current);
      current = [];
    } else current.push([index, amount]);
  }
  if (current.length) result.push(current);
  return result;
}

/** Column path with a 4px rounded data end and a square baseline. */
const column = (x: number, y: number, width: number, height: number) => {
  const r = Math.min(4, width / 2, height);
  return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
};

const cellValue = (row: Row, key: string) => {
  const raw = row[key];
  return raw === null || raw === undefined || Array.isArray(raw)
    ? null
    : Number(raw);
};

type Plot = {
  width: number;
  top: number;
  band: number;
  barWidth: number;
  innerHeight: number;
  x: (index: number) => number;
  y: (amount: number) => number;
};

/** Scales and spacing shared by every layer of one daily chart. */
function makePlot(
  rows: Row[],
  series: Series[],
  kind: "stack" | "line",
  max: number | undefined,
  width: number,
): Plot {
  const totals = rows.map((row) =>
    kind === "stack"
      ? series.reduce((sum, entry) => sum + (cellValue(row, entry.key) ?? 0), 0)
      : Math.max(0, ...series.map((entry) => cellValue(row, entry.key) ?? 0)),
  );
  const dataMax = niceMax(Math.max(0, ...totals));
  const top = max === undefined ? dataMax : Math.min(max, dataMax);
  const innerWidth = Math.max(0, width - PAD.left - PAD.right);
  const innerHeight = HEIGHT - PAD.top - PAD.bottom;
  const band = rows.length > 0 ? innerWidth / rows.length : 0;
  return {
    width,
    top,
    band,
    innerHeight,
    barWidth: Math.max(2, Math.min(24, band * 0.7)),
    x: (index) => PAD.left + band * index + band / 2,
    y: (amount) =>
      PAD.top + innerHeight - (Math.min(amount, top) / top) * innerHeight,
  };
}

function Gridlines({ plot, format }: { plot: Plot; format: Format }) {
  return [0, 0.5, 1].map((fraction) => (
    <g key={fraction}>
      <line
        className={fraction === 0 ? "baseline" : "gridline"}
        x1={PAD.left}
        x2={plot.width - PAD.right}
        y1={plot.y(plot.top * fraction)}
        y2={plot.y(plot.top * fraction)}
      />
      <text
        x={PAD.left - 6}
        y={plot.y(plot.top * fraction) + 3}
        textAnchor="end"
      >
        {format(plot.top * fraction)}
      </text>
    </g>
  ));
}

function DayLabels({ rows, plot }: { rows: Row[]; plot: Plot }) {
  const labelEvery = Math.max(
    1,
    Math.ceil(
      rows.length / Math.max(1, (plot.width - PAD.left - PAD.right) / 70),
    ),
  );
  return rows.map((row, index) =>
    index % labelEvery === 0 ? (
      <text
        key={`label-${String(row.day)}`}
        x={plot.x(index)}
        y={HEIGHT - 6}
        textAnchor="middle"
      >
        {String(row.day).slice(5)}
      </text>
    ) : null,
  );
}

/** One day's stacked parts, bottom to top; the last part gets the rounded end. */
function DayColumn(props: {
  row: Row;
  index: number;
  series: Series[];
  plot: Plot;
}) {
  const { row, index, series, plot } = props;
  let base = 0;
  const parts = series
    .map((entry) => ({ entry, amount: cellValue(row, entry.key) ?? 0 }))
    .filter((part) => part.amount > 0);
  return parts.map((part, partIndex) => {
    const y0 = plot.y(base);
    base += part.amount;
    const y1 = plot.y(base);
    const gap = partIndex < parts.length - 1 ? 2 : 0;
    const height = Math.max(0, y0 - y1 - gap);
    const left = plot.x(index) - plot.barWidth / 2;
    return partIndex === parts.length - 1 ? (
      <path
        key={`${index}-${part.entry.key}`}
        d={column(left, y1, plot.barWidth, height)}
        fill={part.entry.color}
      />
    ) : (
      <rect
        key={`${index}-${part.entry.key}`}
        x={left}
        y={y1 + gap}
        width={plot.barWidth}
        height={height}
        fill={part.entry.color}
      />
    );
  });
}

function LineSeries(props: {
  entry: Series;
  rows: Row[];
  plot: Plot;
  hover: number | null;
}) {
  const { entry, rows, plot, hover } = props;
  const points = rows.map(
    (row, index) => [index, cellValue(row, entry.key)] as const,
  );
  return (
    <g>
      {runs(points).map((run) =>
        run.length === 1 ? (
          <circle
            key={run[0]![0]}
            cx={plot.x(run[0]![0])}
            cy={plot.y(run[0]![1])}
            r={4}
            fill={entry.color}
          />
        ) : (
          <polyline
            key={run[0]![0]}
            className="series-line"
            stroke={entry.color}
            points={run
              .map(([index, amount]) => `${plot.x(index)},${plot.y(amount)}`)
              .join(" ")}
          />
        ),
      )}
      {hover !== null && points[hover]?.[1] != null && (
        <circle
          className="marker"
          cx={plot.x(hover)}
          cy={plot.y(points[hover]![1]!)}
          r={4}
          fill={entry.color}
        />
      )}
    </g>
  );
}

function HitTargets(props: {
  rows: Row[];
  plot: Plot;
  setHover: (index: number | null) => void;
}) {
  const { rows, plot, setHover } = props;
  return rows.map((row, index) => (
    <rect
      key={`hit-${String(row.day)}`}
      className="hit"
      x={PAD.left + plot.band * index}
      y={PAD.top}
      width={plot.band}
      height={plot.innerHeight}
      onMouseEnter={() => setHover(index)}
    />
  ));
}

function DailySvg(props: {
  rows: Row[];
  series: Series[];
  kind: "stack" | "line";
  format: Format;
  plot: Plot;
  shown: number | null;
  active: number | null;
  setHover: (index: number | null) => void;
}) {
  const { rows, series, kind, format, plot, shown, active, setHover } = props;
  return (
    <svg
      width={plot.width}
      height={HEIGHT}
      aria-hidden="true"
      onMouseLeave={() => setHover(null)}
    >
      <Gridlines plot={plot} format={format} />
      <DayLabels rows={rows} plot={plot} />
      {kind === "stack"
        ? rows.map((row, index) => (
            <DayColumn
              key={index}
              row={row}
              index={index}
              series={series}
              plot={plot}
            />
          ))
        : series.map((entry) => (
            <LineSeries
              key={entry.key}
              entry={entry}
              rows={rows}
              plot={plot}
              hover={shown}
            />
          ))}
      {shown !== null && kind === "line" && (
        <line
          className="crosshair"
          x1={plot.x(shown)}
          x2={plot.x(shown)}
          y1={PAD.top}
          y2={PAD.top + plot.innerHeight}
        />
      )}
      {active !== null && (
        <rect
          className="day-focus"
          x={PAD.left + plot.band * active}
          y={PAD.top}
          width={plot.band}
          height={plot.innerHeight}
        />
      )}
      <HitTargets rows={rows} plot={plot} setHover={setHover} />
    </svg>
  );
}

const readout = (row: Row, series: Series[], format: Format) =>
  `${String(row.day)}: ${series
    .map((entry) => `${entry.label} ${format(cellValue(row, entry.key))}`)
    .join(", ")}`;

function DayTooltip(props: {
  row: Row;
  index: number;
  series: Series[];
  format: Format;
  plot: Plot;
}) {
  const { row, index, series, format, plot } = props;
  return (
    <div
      className="tooltip"
      style={{
        left: Math.min(
          Math.max(plot.x(index) - 80, 0),
          Math.max(0, plot.width - 170),
        ),
      }}
      aria-hidden="true"
    >
      <b>{String(row.day)}</b>
      {series.map((entry) => (
        <span key={entry.key}>
          <i style={{ background: entry.color }} aria-hidden="true" />
          {entry.label}
          <em>{format(cellValue(row, entry.key))}</em>
        </span>
      ))}
    </div>
  );
}

/** Where each navigation key moves the focused day; null means none yet. */
export const DAY_KEYS: Record<
  string,
  (current: number | null, last: number) => number
> = {
  ArrowLeft: (current) => (current === null ? 0 : Math.max(0, current - 1)),
  ArrowRight: (current, last) =>
    current === null ? 0 : Math.min(last, current + 1),
  Home: () => 0,
  End: (_current, last) => last,
};

/** The keyboard-selected day of a chart: one tab stop, arrows move, Escape clears. */
function useDayFocus(days: number, clearHover: () => void) {
  const [active, setActive] = useState<number | null>(null);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      setActive(null);
      clearHover();
      return;
    }
    const move = DAY_KEYS[event.key];
    if (!move || days === 0) return;
    event.preventDefault();
    setActive(move(active, days - 1));
  };
  return {
    active,
    handlers: {
      tabIndex: days > 0 ? 0 : undefined,
      onKeyDown,
      onFocus: () => setActive((current) => current ?? 0),
      onBlur: () => setActive(null),
    },
  };
}

/**
 * The chart's accessible name, and whether it needs its own legend: the
 * contributor chips already name the harnesses a legend would.
 */
const chartNaming = (
  panel: { label: string; harnessChips: boolean },
  series: Series[],
) => ({
  name: panel.label || series.map((entry) => entry.label).join(", "),
  legend: !(
    panel.harnessChips && series.every((entry) => entry.key in HARNESS_LABEL)
  ),
});

/**
 * Daily trend as stacked columns (mutually exclusive parts of one total) or
 * lines (a rate per series). One y-axis. The chart is one tab stop: Left/Right,
 * Home and End move between days, Escape clears; hovering or focusing a day
 * shows every series value for that day.
 */
export function DailyChart({
  rows: sparse,
  series,
  kind,
  format,
  max,
}: {
  rows: Row[];
  series: Series[];
  kind: "stack" | "line";
  format: Format;
  /** Ceiling for the axis; the axis still fits the data when it stays lower. */
  max?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const panel = useContext(PanelContext);
  const [hover, setHover] = useState<number | null>(null);
  const rows = continuousDays(sparse);
  const { active, handlers } = useDayFocus(rows.length, () => setHover(null));
  const plot = makePlot(rows, series, kind, max, width);
  const shown = hover ?? active;
  const shownRow = rows[shown ?? -1];
  const activeRow = rows[active ?? -1];
  const { name, legend } = chartNaming(panel, series);
  if (rows.length === 0)
    return (
      <div className="chart">
        <p className="empty">No rows for the selected range.</p>
      </div>
    );
  return (
    <div
      className="chart"
      ref={ref}
      role="group"
      aria-label={`${name} daily chart`}
      {...handlers}
    >
      {legend && <Legend series={series} />}
      {width > 0 && (
        <DailySvg
          rows={rows}
          series={series}
          kind={kind}
          format={format}
          plot={plot}
          shown={shown}
          active={active}
          setHover={setHover}
        />
      )}
      <p className="sr-only" aria-live="polite">
        {activeRow ? readout(activeRow, series, format) : ""}
      </p>
      {shownRow && (
        <DayTooltip
          row={shownRow}
          index={shown!}
          series={series}
          format={format}
          plot={plot}
        />
      )}
    </div>
  );
}

export type BarItem = {
  key: string;
  label: ReactNode;
  value: number | null;
  color: string;
  detail?: ReactNode;
};

/** Horizontal bars from one baseline with the value at the tip. */
export function BarList({
  items,
  format,
  max,
}: {
  items: BarItem[];
  format: Format;
  max?: number;
}) {
  const top = max ?? Math.max(0, ...items.map((item) => item.value ?? 0));
  return (
    <ul className="bar-list">
      {items.map((item) => (
        <li key={item.key}>
          <span className="bar-label">{item.label}</span>
          <span className="bar-track">
            {item.value !== null && top > 0 && (
              <span
                className="bar"
                style={{
                  width: `${Math.max(0.5, (100 * item.value) / top)}%`,
                  background: item.color,
                }}
              />
            )}
            <span className="bar-value">
              {item.value === null ? "No value" : format(item.value)}
            </span>
          </span>
          {item.detail && <span className="bar-detail">{item.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

export type Column = {
  key: string;
  label: string;
  numeric?: boolean;
  render?: (row: Row) => ReactNode;
};

const cell = (value: Scalar | Scalar[] | undefined): ReactNode =>
  value === null || value === undefined
    ? "—"
    : Array.isArray(value)
      ? value.join(", ")
      : typeof value === "number"
        ? new Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(
            value,
          )
        : String(value);

export type Sort = { key: string; descending: boolean };

/** A cell's value as a number when it is one (ClickHouse sends 64-bit integers as strings). */
const numeric = (value: Scalar | Scalar[] | undefined): number | null => {
  if (typeof value === "number") return value;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

/** Ascending order; null and missing values sort after every value in both directions. */
function compareCells(
  a: Scalar | Scalar[] | undefined,
  b: Scalar | Scalar[] | undefined,
): number {
  const left = numeric(a);
  const right = numeric(b);
  if (left !== null && right !== null) return left - right;
  return String(cell(a)).localeCompare(String(cell(b)), "en", {
    numeric: true,
  });
}

const isBlank = (value: Scalar | Scalar[] | undefined) =>
  value === null || value === undefined;

/**
 * Rows ordered by one column. The server's All row (harness '') keeps its
 * position; every other row is reordered around it.
 */
export function sortRows(rows: Row[], sort: Sort | null): Row[] {
  if (!sort) return rows;
  const pinned = (row: Row) => row.harness === "";
  const sign = sort.descending ? -1 : 1;
  const sorted = rows
    .filter((row) => !pinned(row))
    .sort((a, b) => {
      const x = a[sort.key];
      const y = b[sort.key];
      if (isBlank(x) || isBlank(y))
        return Number(isBlank(x)) - Number(isBlank(y));
      return sign * compareCells(x, y);
    });
  let next = 0;
  return rows.map((row) => (pinned(row) ? row : sorted[next++]));
}

/** A column header that sorts its table: numbers start largest first, text A to Z. */
function SortHeader({
  column,
  sort,
  onSort,
}: {
  column: Column;
  sort: Sort | null;
  onSort: (sort: Sort | null) => void;
}) {
  const active = sort?.key === column.key;
  const firstDescending = Boolean(column.numeric);
  const toggle = () =>
    onSort(
      !active
        ? { key: column.key, descending: firstDescending }
        : sort.descending === firstDescending
          ? { key: column.key, descending: !firstDescending }
          : null,
    );
  return (
    <th
      scope="col"
      className={column.numeric ? "num" : undefined}
      aria-sort={
        active ? (sort.descending ? "descending" : "ascending") : undefined
      }
    >
      <button type="button" className="sort" onClick={toggle}>
        {column.label}
        <span className="sort-mark" aria-hidden="true">
          {active ? (sort.descending ? "▼" : "▲") : "↕"}
        </span>
      </button>
    </th>
  );
}

export function DataTable({
  columns,
  rows,
  empty = "No rows for the selected range.",
  total = num(rows[0]?.total_rows),
  label,
}: {
  columns: Column[];
  rows: Row[];
  empty?: string;
  /**
   * Rows the source holds before a display limit; shows "Showing N of M" when larger.
   * Defaults to the `total_rows` column a limited query returns (`count() OVER ()`).
   */
  total?: number;
  /** Accessible name of the scrollable table region; defaults to the panel title. */
  label?: string;
}) {
  const panel = useContext(PanelContext);
  const [sort, setSort] = useState<Sort | null>(null);
  const name = label ?? panel.label;
  if (rows.length === 0) return <p className="empty">{empty}</p>;
  const sortable = (column: Column) =>
    rows.length > 1 && rows.some((row) => !isBlank(row[column.key]));
  return (
    <>
      {total > rows.length && (
        <p className="table-count">
          Showing {rows.length.toLocaleString("en")} of{" "}
          {total.toLocaleString("en")}
        </p>
      )}
      <div
        className="table-wrap"
        tabIndex={0}
        role={name ? "region" : undefined}
        aria-label={name || undefined}
      >
        <table>
          <thead>
            <tr>
              {columns.map((column) =>
                sortable(column) ? (
                  <SortHeader
                    key={column.key}
                    column={column}
                    sort={sort}
                    onSort={setSort}
                  />
                ) : (
                  <th
                    key={column.key}
                    scope="col"
                    className={column.numeric ? "num" : undefined}
                  >
                    {column.label}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {sortRows(rows, sort).map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={column.numeric ? "num" : undefined}
                  >
                    {column.render ? column.render(row) : cell(row[column.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** The chart's data as a table, for exact values and non-visual reading. */
export function TableView({
  columns,
  rows,
}: {
  columns: Column[];
  rows: Row[];
}) {
  return (
    <details className="table-view">
      <summary>View as table</summary>
      <DataTable columns={columns} rows={rows} />
    </details>
  );
}
