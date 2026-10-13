import { expect, test } from "bun:test";
import { isMutedRate } from "./components";
import { HARNESS_COLOR, SERIES } from "./format";
import { HARNESSES } from "./harnesses.generated";
import { HARNESS_TOKENS, PART_TOKENS } from "./tokens";

test("non-harness series never share a harness colour", () => {
  const harness = new Set<string>(HARNESS_TOKENS);
  for (const color of [...PART_TOKENS, ...SERIES]) {
    expect(harness.has(color)).toBe(false);
  }
});

test("every registry harness has its own identity slot", () => {
  expect(HARNESS_TOKENS.length).toBeGreaterThanOrEqual(HARNESSES.length);
  const colors = HARNESSES.map((harness) => HARNESS_COLOR[harness]);
  expect(new Set(colors).size).toBe(HARNESSES.length);
  for (const color of colors) {
    expect(HARNESS_TOKENS as readonly string[]).toContain(color);
  }
});

test("a rate is muted below the server threshold and not at it", () => {
  expect(isMutedRate(19, 20)).toBe(true);
  expect(isMutedRate(20, 20)).toBe(false);
});
