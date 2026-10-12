import { expect, test } from "bun:test";
import { appliedDetail, metricLabel, subjectLabel } from "./Interventions";

test("applied detail formats the server's in-range count and verdict parts", () => {
  expect(
    appliedDetail({
      applied_in_range: 1,
      validated: 1,
      regressed: 1,
      inconclusive: 0,
      not_evaluated: 2,
    }),
  ).toBe(
    "1 in range · 1 validated · 1 regressed · 0 inconclusive · 2 not evaluated",
  );
});

test("subject and metric labels read the structured fields", () => {
  expect(
    subjectLabel({
      kind: "cluster",
      tool_family: "shell",
      failure_class: "cmd not found",
    }),
  ).toBe("shell · cmd not found");
  expect(
    subjectLabel({
      kind: "correction",
      project: "repo",
      correction_kind: "wrong_scope",
    }),
  ).toBe("repo · wrong scope");
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
