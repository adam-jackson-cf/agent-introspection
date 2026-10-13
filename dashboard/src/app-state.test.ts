import { describe, expect, test } from "bun:test";
import type { SessionResponse } from "./contracts";
import { matchingSession, visibleFailure } from "./app-data";
import { apiQuery, filterQuery, lastDays, presetFor } from "./app-state";
import { HARNESS_COLOR, SERIES } from "./format";

const WEEK = {
  start: "2026-09-24T12:00:00.000Z",
  end: "2026-10-01T12:00:00.000Z",
  harness: "" as const,
};

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

describe("live presets", () => {
  test("a preset is named by its range, not by the window's length", () => {
    expect(presetFor({ ...WEEK, range: 7 })).toBe("Last 7 days");
    expect(presetFor(WEEK)).toBe("Custom range");
  });
  test("a preset link carries its length, so reopening it ends at the new now", () => {
    const query = filterQuery({ ...lastDays(30), harness: "", range: 30 });
    expect(query.get("range")).toBe("30");
    expect(query.has("start")).toBe(false);
    expect(query.has("end")).toBe(false);
  });
  test("All leaves no empty harness parameter; a named harness is kept", () => {
    expect(filterQuery({ ...WEEK, range: 7 }).has("harness")).toBe(false);
    expect(filterQuery({ ...WEEK, harness: "codex_exec" }).get("harness")).toBe(
      "codex_exec",
    );
  });
  test("a custom range link keeps its fixed window", () => {
    const query = filterQuery(WEEK);
    expect(query.get("start")).toBe(WEEK.start);
    expect(query.get("end")).toBe(WEEK.end);
    expect(query.has("range")).toBe(false);
  });
  test("the API always receives the resolved window", () => {
    const query = apiQuery({ ...WEEK, range: 7 });
    expect(query.get("start")).toBe(WEEK.start);
    expect(query.get("end")).toBe(WEEK.end);
    expect(query.has("range")).toBe(false);
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
