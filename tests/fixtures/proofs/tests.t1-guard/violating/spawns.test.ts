import { expect, test } from "bun:test";

test("a T1 bun test that starts a process", () => {
  const proc = Bun.spawnSync(["true"], { timeout: 5000 });
  expect(proc.exitCode).toBe(0);
});
