import { describe, expect, test } from "bun:test";
import { ttlCache } from "./server";

describe("ttlCache", () => {
  test("shares a pending load, and drops a rejected one so the next load retries", async () => {
    const cached = ttlCache<number>(60_000, 4, () => 0);
    let calls = 0;
    const load = async () => {
      calls += 1;
      if (calls === 1) throw new Error("Code: 241");
      return calls;
    };
    await expect(cached("k", load)).rejects.toThrow("Code: 241");
    expect(await cached("k", load)).toBe(2);
    expect(await cached("k", load)).toBe(2);
    expect(calls).toBe(2);
  });
});
