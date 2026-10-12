import { HARNESSES, type Harness } from "./contracts";
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

export const shortTime = (value: unknown) =>
  typeof value === "string" ? value.slice(0, 16).replace("T", " ") : "—";
