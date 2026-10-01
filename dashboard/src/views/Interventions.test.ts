import { expect, test } from "bun:test";
import type { Row } from "../contracts";
import {
  metricLabel,
  prePost,
  subjectLabel,
  type DailyFacts,
} from "./Interventions";

const proposal: Row = {
  detector: "facts.failure_cluster",
  tool_family: "shell",
  failure_class: "cmd not found",
  harnesses: ["codex_exec"],
  target: "t",
  tier: "established_tool",
  // 14:00 London (BST) on 10 September.
  applied_at: "2026-09-10T13:00:00Z",
};
/** Failure-cluster tasks on one London day. */
const day = (d: string, tasks: number, harness = "codex_exec"): Row => ({
  day: d,
  harness,
  tool_family: "shell",
  failure_class: "cmd not found",
  occurrences: tasks,
  tasks,
});
const tasks = ["07", "08", "09", "10", "11", "12", "13"].map((d) => ({
  day: `2026-09-${d}`,
  harness: "codex_exec",
  tasks: 10,
}));
const facts = (clusters: Row[], corrections: Row[] = []): DailyFacts => ({
  clusters,
  corrections,
  tasks,
});

test("the application day counts in neither window", () => {
  const result = prePost(
    proposal,
    facts([
      day("2026-09-08", 1),
      day("2026-09-09", 1),
      day("2026-09-10", 50),
      day("2026-09-11", 2),
      day("2026-09-12", 2),
    ]),
    "2026-09-07T00:00:00Z",
    "2026-09-14T00:00:00Z",
  );

  expect(result.days).toBe(2);
  expect(result.pre_matched).toBe(2);
  expect(result.post_matched).toBe(4);
  expect(result.pre_tasks).toBe(20);
  expect(result.post_tasks).toBe(20);
});

test("windows use the London calendar day of the application", () => {
  // 23:30 UTC on 9 September is 00:30 on 10 September in London.
  const late = { ...proposal, applied_at: "2026-09-09T23:30:00Z" };

  const result = prePost(
    late,
    facts([day("2026-09-09", 3), day("2026-09-10", 50), day("2026-09-11", 5)]),
    "2026-09-07T00:00:00Z",
    "2026-09-14T00:00:00Z",
  );

  expect(result.pre_matched).toBe(3);
  expect(result.post_matched).toBe(5);
});

test("a range starting at London midnight keeps its first whole day", () => {
  // 23:00 UTC on 1 September and 19 September are London midnights (BST).
  const result = prePost(
    proposal,
    facts([day("2026-09-02", 7), day("2026-09-10", 50), day("2026-09-18", 1)]),
    "2026-09-01T23:00:00Z",
    "2026-09-19T23:00:00Z",
  );

  expect(result.days).toBe(8);
  expect(result.pre_matched).toBe(7);
  expect(result.post_matched).toBe(1);
});

test("a cluster counts only the metric's harnesses and its own cluster", () => {
  const result = prePost(
    { ...proposal, metric_harnesses: ["codex_exec"] },
    facts([
      day("2026-09-09", 2),
      day("2026-09-09", 9, "oh-my-pi"),
      { ...day("2026-09-09", 9), failure_class: "other" },
    ]),
    "2026-09-07T00:00:00Z",
    "2026-09-14T00:00:00Z",
  );

  expect(result.pre_matched).toBe(2);
  expect(result.pre_rate).toBe(2 / 20);
});

test("a repeated correction compares its kind per labelled task in its project", () => {
  const correction: Row = {
    detector: "facts.repeated_correction",
    project: "repo",
    correction_kind: "wrong_scope",
    harnesses: ["claude-code"],
    applied_at: "2026-09-10T13:00:00Z",
  };
  const row = (d: string, kind: string, count: number, project = "repo") => ({
    day: d,
    harness: "claude-code",
    project,
    correction_kind: kind,
    tasks: count,
    corrected: kind === "" ? 0 : count,
  });

  const result = prePost(
    correction,
    facts(
      [],
      [
        row("2026-09-09", "wrong_scope", 2),
        row("2026-09-09", "incomplete", 1),
        row("2026-09-09", "", 7),
        row("2026-09-09", "wrong_scope", 5, "other"),
        row("2026-09-11", "", 4),
      ],
    ),
    "2026-09-07T00:00:00Z",
    "2026-09-14T00:00:00Z",
  );

  expect(result.pre_matched).toBe(2);
  expect(result.pre_tasks).toBe(10);
  expect(result.post_matched).toBe(0);
  expect(result.post_tasks).toBe(4);
  expect(result.subject).toBe("repo · wrong scope");
});

test("subject and metric labels read the structured fields", () => {
  expect(subjectLabel(proposal)).toBe("shell · cmd not found");
  expect(metricLabel({ metric: null })).toBeNull();
  expect(
    metricLabel({
      metric: "cluster_task_rate",
      max_ratio: 0.5,
      baseline_days: 14,
      evaluation_days: 7,
      metric_harnesses: ["claude-code"],
    }),
  ).toBe(
    "cluster_task_rate ≤ 0.5× baseline · 7 d after vs 14 d before · Claude Code",
  );
});
