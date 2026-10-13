import { HARNESSES, HARNESS_LABEL, type Harness } from "./harnesses.generated";
import { EFFORT_TOKENS, HARNESS_TOKENS, PART_TOKENS } from "./tokens";

export { HARNESS_LABEL };

/**
 * Harness identity colors: the categorical harness slots in docs/ux/DESIGN.md, read from
 * the token mirror, one per harness in registry order. Color follows the harness
 * everywhere; it never follows rank.
 */
export const HARNESS_COLOR = Object.fromEntries(
  HARNESSES.map((harness, index) => [harness, HARNESS_TOKENS[index]]),
) as Record<Harness, string>;
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

/** Verification kinds in display order, as stored in `tool_calls.verification_kinds`. */
export const VERIFICATION_KINDS = [
  "unit_test",
  "integration_e2e",
  "smoke",
  "lint_format",
  "typecheck",
  "ci_remote",
] as const;
export const VERIFICATION_LABEL: Record<string, string> = {
  unit_test: "Unit tests",
  integration_e2e: "Integration and end-to-end",
  smoke: "Smoke tests",
  lint_format: "Lint and format",
  typecheck: "Type check",
  ci_remote: "Remote CI",
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

/** A text cell, "—" when the value is empty. */
export const orDash = (value: unknown): string => String(value ?? "") || "—";

export const shortTime = (value: unknown) =>
  typeof value === "string" ? value.slice(0, 16).replace("T", " ") : "—";

/** Registry prose without its inline-code backticks, for reading rather than copying. */
export const plain = (text: string): string => text.replaceAll("`", "");
