const color = (token: string) => `var(--color-${token})`;

export const HARNESS_TOKENS = [
  color("cat-1"),
  color("cat-2"),
  color("cat-3"),
  color("cat-4"),
  color("cat-5"),
  color("cat-9"),
  color("cat-10"),
  color("cat-11"),
] as const;

export const PART_TOKENS = [
  color("cat-6"),
  color("cat-7"),
  color("cat-8"),
] as const;

export const EFFORT_TOKENS = {
  low: color("seq-effort-1"),
  medium: color("seq-effort-2"),
  high: color("seq-effort-3"),
  xhigh: color("seq-effort-4"),
  mixed: color("effort-mixed"),
  unset: color("other"),
} as const;

export const OTHER_TOKEN = color("other");
