import { expect, test } from "bun:test";

test.skip("a bun case that is skipped", () => {
  expect(1).toBe(1);
});
