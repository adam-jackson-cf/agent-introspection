import { expect, test } from "bun:test";
import { HARNESS_COLOR, SERIES } from "./format";

test("non-harness series never share a harness colour", () => {
  const harness = new Set(Object.values(HARNESS_COLOR));
  for (const color of SERIES) expect(harness.has(color)).toBe(false);
});
