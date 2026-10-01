import { HARNESSES, type Harness, type Row } from "./contracts";
import { EFFORT_TOKENS, HARNESS_TOKENS, PART_TOKENS } from "./tokens";

/**
 * Harness identity colors: categorical `cat-1`…`cat-5` in docs/ux/DESIGN.md, read from
 * the token mirror. Color follows the harness everywhere; it never follows rank.
 */
export const HARNESS_COLOR: Record<Harness, string> = {
  "oh-my-pi": HARNESS_TOKENS[0],
  "codex-app-server": HARNESS_TOKENS[1],
  codex_cli_rs: HARNESS_TOKENS[2],
  codex_exec: HARNESS_TOKENS[3],
  "claude-code": HARNESS_TOKENS[4],
};
export const HARNESS_LABEL: Record<Harness, string> = {
  "oh-my-pi": "omp",
  "codex-app-server": "Codex app-server",
  codex_cli_rs: "Codex CLI",
  codex_exec: "Codex exec",
  "claude-code": "Claude Code",
};
/**
 * Categorical slots for non-harness series, in fixed order: `cat-6`…`cat-8`
 * (PART_TOKENS), so a series never shares a colour with a harness.
 */
export const SERIES = [...PART_TOKENS];

/**
 * Effort is ordinal: one blue ramp low → xhigh (`seq-effort-*` in DESIGN.md).
 * The data holds low, medium, high, xhigh, unset, and mixed; unset (provider
 * default) and mixed sit apart from the ramp.
 */
export const EFFORT_ORDER = [
  "low",
  "medium",
  "high",
  "xhigh",
  "mixed",
  "unset",
];
export const EFFORT_COLOR: Record<string, string> = { ...EFFORT_TOKENS };
export const effortRank = (effort: string) => {
  const index = EFFORT_ORDER.indexOf(effort);
  return index === -1 ? EFFORT_ORDER.length : index;
};

export const isHarness = (value: unknown): value is Harness =>
  typeof value === "string" && (HARNESSES as readonly string[]).includes(value);
export const harnessLabel = (value: unknown) =>
  isHarness(value) ? HARNESS_LABEL[value] : String(value);

const compact = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const whole = new Intl.NumberFormat("en");

export const num = (value: unknown): number => {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};
/** null when the value is absent, so callers render "—", never 0. */
export const maybe = (value: unknown): number | null =>
  value === null || value === undefined || value === "" ? null : num(value);

export const fmtCount = (value: number | null) =>
  value === null
    ? "—"
    : Math.abs(value) >= 10_000
      ? compact.format(value)
      : whole.format(Math.round(value));
export const fmtPct = (value: number | null, digits = 1) =>
  value === null ? "—" : `${value.toFixed(digits)}%`;
export const fmtSeconds = (value: number | null) => {
  if (value === null) return "—";
  if (value >= 3600) return `${(value / 3600).toFixed(1)} h`;
  if (value >= 60) return `${(value / 60).toFixed(1)} min`;
  return `${value.toFixed(value >= 10 ? 0 : 1)} s`;
};

/** Token-weighted ratio: aggregate before division; no denominator gives null. */
export const ratio = (numerator: number, denominator: number) =>
  denominator > 0 ? (100 * numerator) / denominator : null;

/** Sums numeric fields across per-harness rows: All is the union of each. */
export function sumRows(rows: Row[], keys: string[]): Record<string, number> {
  const totals: Record<string, number> = Object.fromEntries(
    keys.map((key) => [key, 0]),
  );
  for (const row of rows)
    for (const key of keys) totals[key] = totals[key]! + num(row[key]);
  return totals;
}

/** Groups rows by a key and sums numeric fields within each group. */
export function groupSum(
  rows: Row[],
  by: string,
  keys: string[],
): Map<string, Record<string, number>> {
  const groups = new Map<string, Row[]>();
  for (const row of rows) {
    const key = String(row[by]);
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  return new Map(
    [...groups].map(([key, members]) => [key, sumRows(members, keys)]),
  );
}

export const shortTime = (value: unknown) =>
  typeof value === "string" ? value.slice(0, 16).replace("T", " ") : "—";

/**
 * Pivots long daily rows into one row per day with a column per series. The
 * value function receives the summed numeric fields of that day and series.
 */
export function pivotDaily(
  rows: Row[],
  seriesOf: (row: Row) => string,
  fields: string[],
  value: (sums: Record<string, number>) => number | null,
): Row[] {
  const days = new Map<string, Map<string, Row[]>>();
  for (const row of rows) {
    const day = String(row.day);
    const bySeries = days.get(day) ?? new Map<string, Row[]>();
    const series = seriesOf(row);
    bySeries.set(series, [...(bySeries.get(series) ?? []), row]);
    days.set(day, bySeries);
  }
  return [...days.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, bySeries]) => {
      const out: Row = { day };
      for (const [series, members] of bySeries)
        out[series] = value(sumRows(members, fields));
      return out;
    });
}
