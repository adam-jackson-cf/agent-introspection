import { expect, test } from "bun:test";

test("a bounded process call", async () => {
  const proc = Bun.spawn(["true"], { stdout: "pipe", timeout: 5000 });
  expect(await proc.exited).toBe(0);
});
