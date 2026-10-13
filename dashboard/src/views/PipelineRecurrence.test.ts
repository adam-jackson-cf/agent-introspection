import { expect, test } from "bun:test";
import { dailyByHarness } from "./Pipeline";
import { londonDate } from "./Recurrence";

test("unresolved hook rows chart as their own series on the day they occurred", () => {
  const daily = dailyByHarness([
    { day: "2026-09-30", harness: "codex-cli", n: 5 },
    { day: "2026-09-30", harness: "", n: 5 },
  ]);
  expect(daily.get("2026-09-30")).toEqual({
    day: "2026-09-30",
    "codex-cli": 5,
    unresolved: 5,
  });
});

test("window-end label is the Europe/London date, which the SQL week cuts at", () => {
  // 23:30 UTC in BST is already the next London day.
  expect(londonDate("2026-07-01T23:30:00.000Z")).toBe("2026-07-02");
  expect(londonDate("2026-01-01T23:30:00.000Z")).toBe("2026-01-01");
});
