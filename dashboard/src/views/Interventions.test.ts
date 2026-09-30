import { expect, test } from "bun:test";
import { prePost } from "./Interventions";

const proposal = {
  harness: "codex_exec",
  signature: "sig",
  target: "t",
  tier: "established_tool",
  // 14:00 London (BST) on 10 September.
  applied_at: "2026-09-10T13:00:00Z",
};
const day = (d: string, occurrences: number) => ({
  day: d,
  harness: "codex_exec",
  signature: "sig",
  occurrences,
});
const tasks = ["07", "08", "09", "10", "11", "12", "13"].map((d) => ({
  day: `2026-09-${d}`,
  harness: "codex_exec",
  tasks: 10,
}));

test("the application day counts in neither window", () => {
  const signatures = [
    day("2026-09-08", 1),
    day("2026-09-09", 1),
    day("2026-09-10", 50),
    day("2026-09-11", 2),
    day("2026-09-12", 2),
  ];

  const result = prePost(
    proposal,
    signatures,
    tasks,
    "2026-09-07T00:00:00Z",
    "2026-09-14T00:00:00Z",
  );

  expect(result.days).toBe(2);
  expect(result.pre_occurrences).toBe(2);
  expect(result.post_occurrences).toBe(4);
  expect(result.pre_tasks).toBe(20);
  expect(result.post_tasks).toBe(20);
});

test("windows use the London calendar day of the application", () => {
  // 23:30 UTC on 9 September is 00:30 on 10 September in London.
  const late = { ...proposal, applied_at: "2026-09-09T23:30:00Z" };

  const result = prePost(
    late,
    [day("2026-09-09", 3), day("2026-09-10", 50), day("2026-09-11", 5)],
    tasks,
    "2026-09-07T00:00:00Z",
    "2026-09-14T00:00:00Z",
  );

  expect(result.pre_occurrences).toBe(3);
  expect(result.post_occurrences).toBe(5);
});

test("a range starting at London midnight keeps its first whole day", () => {
  // 23:00 UTC on 1 September and 19 September are London midnights (BST).
  const result = prePost(
    proposal,
    [day("2026-09-02", 7), day("2026-09-10", 50), day("2026-09-18", 1)],
    tasks,
    "2026-09-01T23:00:00Z",
    "2026-09-19T23:00:00Z",
  );

  expect(result.days).toBe(8);
  expect(result.pre_occurrences).toBe(7);
  expect(result.post_occurrences).toBe(1);
});
