import { expect, test } from "bun:test";
import { isMutedRate } from "./components";
import { HARNESS_COLOR, SERIES } from "./format";

test("non-harness series never share a harness colour", () => {
  const harness = new Set(Object.values(HARNESS_COLOR));
  for (const color of SERIES) expect(harness.has(color)).toBe(false);
});

test("a rate is muted below the server threshold and not at it", () => {
  expect(isMutedRate(19, 20)).toBe(true);
  expect(isMutedRate(20, 20)).toBe(false);
});
