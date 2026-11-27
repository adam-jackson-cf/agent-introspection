import { expect, test } from "bun:test";

test("an unbounded process call", async () => {
  const proc = Bun.spawn(["true"], { stdout: "pipe" });
  expect(await proc.exited).toBe(0);
});
