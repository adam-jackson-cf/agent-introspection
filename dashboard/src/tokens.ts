/**
 * Mirrors docs/ux/DESIGN.md. Change it first, then tokens.css; this module only
 * references the custom properties defined there, so values live in one file.
 */
const color = (token: string) => `var(--color-${token})`;

/** Harness identity, categorical `cat-1`…`cat-5`, in DESIGN.md order. */
export const HARNESS_TOKENS = [
  color("cat-1"),
  color("cat-2"),
  color("cat-3"),
  color("cat-4"),
  color("cat-5"),
] as const;

/** Parts of a total (non-harness series), categorical `cat-6`…`cat-8`. */
export const PART_TOKENS = [
  color("cat-6"),
  color("cat-7"),
  color("cat-8"),
] as const;

/** Reasoning effort, ordinal low → xhigh, then the off-ramp tokens. */
export const EFFORT_TOKENS = {
  low: color("seq-effort-1"),
  medium: color("seq-effort-2"),
  high: color("seq-effort-3"),
  xhigh: color("seq-effort-4"),
  mixed: color("effort-mixed"),
  unset: color("other"),
} as const;

/** Neutral for residual or unknown groups. */
export const OTHER_TOKEN = color("other");
