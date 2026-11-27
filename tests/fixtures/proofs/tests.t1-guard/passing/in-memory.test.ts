import { expect, test } from "bun:test";

test("a T1 bun test computes in memory", () => {
  expect([3, 1, 2].sort()).toEqual([1, 2, 3]);
});
