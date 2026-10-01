import { describe, expect, test } from "bun:test";
import type { SessionResponse } from "./contracts";
import { matchingSession, visibleFailure } from "./app-data";
import { lastDays, presetFor } from "./app-state";
import { HARNESS_COLOR, SERIES } from "./format";

const NOW = Date.parse("2026-10-01T12:00:00.000Z");

describe("visibleFailure", () => {
  test("a registry failure stays visible while the view failure is cleared", () => {
    expect(visibleFailure("registry down", "")).toBe("registry down");
  });
  test("a view failure shows once the registry has loaded", () => {
    expect(visibleFailure("", "view broke")).toBe("view broke");
  });
});

test("interrupt and steer series avoid harness colours", () => {
  const harness = new Set(Object.values(HARNESS_COLOR));
  expect(harness.has(SERIES[0]!)).toBe(false);
  expect(harness.has(SERIES[1]!)).toBe(false);
});

describe("presetFor", () => {
  test("live window is a preset", () => {
    const end = "2026-10-01T12:00:00.000Z";
    const start = "2026-09-24T12:00:00.000Z";
    expect(presetFor({ start, end, harness: "" }, NOW)).toBe("Last 7 days");
  });
  test("historical fixed-length window is a custom range", () => {
    const f = {
      start: "2026-08-01T00:00:00.000Z",
      end: "2026-08-08T00:00:00.000Z",
      harness: "" as const,
    };
    expect(presetFor(f, NOW)).toBe("Custom range");
  });
  test("lastDays round-trips to its preset", () => {
    expect(presetFor({ ...lastDays(30), harness: "" })).toBe("Last 30 days");
  });
});

describe("matchingSession", () => {
  const response: SessionResponse = {
    harness: "oh-my-pi",
    session: "a",
    queriedAt: "2026-10-01T00:00:00Z",
    data: {},
  };
  const loc = (session: string) =>
    ({ kind: "session", harness: "oh-my-pi", session }) as never;
  test("accepts the requested session", () => {
    expect(matchingSession(loc("a"), response)).toBe(response);
  });
  test("rejects the previous session's response", () => {
    expect(matchingSession(loc("b"), response)).toBeNull();
  });
});
