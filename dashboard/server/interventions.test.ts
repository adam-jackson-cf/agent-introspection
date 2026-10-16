import { expect, test } from "bun:test";
import type { Row } from "../src/contracts";
import { interventionsData, prePost } from "./interventions";
import { VIEW_QUERIES } from "./views";

const proposal: Row = {
  id: "p1",
  detector: "facts.failure_cluster",
  tool_family: "shell",
  failure_class: "cmd not found",
  harnesses: ["codex_exec"],
  target: "t",
  tier: "established_tool",
  state: "applied",
  // 14:00 London (BST) on 10 September.
  applied_at: "2026-09-10T13:00:00Z",
};
const cluster = (d: string, tasks: number, harness = "codex_exec"): Row => ({
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
const facts = (clusters: Row[]) => ({ clusters, tasks });
const RANGE = ["2026-09-07T00:00:00Z", "2026-09-14T00:00:00Z"] as const;

test("the application day counts in neither window, and the rate change is a column", () => {
  const result = prePost(
    proposal,
    facts([
      cluster("2026-09-08", 1),
      cluster("2026-09-09", 1),
      cluster("2026-09-10", 50),
      cluster("2026-09-11", 2),
      cluster("2026-09-12", 2),
    ]),
    ...RANGE,
  );
  expect(result.days).toBe(2);
  expect(result.pre_matched).toBe(2);
  expect(result.post_matched).toBe(4);
  expect(result.pre_tasks).toBe(20);
  expect(result.post_tasks).toBe(20);
  // rate 2/20 -> 4/20 is +100 %.
  expect(result.change_pct).toBeCloseTo(100);
});

test("rate change is null without a pre rate or when it is zero", () => {
  const none = prePost(proposal, facts([cluster("2026-09-11", 2)]), ...RANGE);
  expect(none.pre_rate).toBe(0);
  expect(none.change_pct).toBeNull();
  const noTasks = prePost(proposal, { clusters: [], tasks: [] }, ...RANGE);
  expect(noTasks.pre_rate).toBeNull();
  expect(noTasks.change_pct).toBeNull();
});

test("windows use the London calendar day of the application", () => {
  // 23:30 UTC on 9 September is 00:30 on 10 September in London.
  const result = prePost(
    { ...proposal, applied_at: "2026-09-09T23:30:00Z" },
    facts([
      cluster("2026-09-09", 3),
      cluster("2026-09-10", 50),
      cluster("2026-09-11", 5),
    ]),
    ...RANGE,
  );
  expect(result.pre_matched).toBe(3);
  expect(result.post_matched).toBe(5);
});

test("a range starting at London midnight keeps its first whole day", () => {
  // 23:00 UTC on 1 September and 19 September are London midnights (BST).
  const result = prePost(
    proposal,
    facts([
      cluster("2026-09-02", 7),
      cluster("2026-09-10", 50),
      cluster("2026-09-18", 1),
    ]),
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
      cluster("2026-09-09", 2),
      cluster("2026-09-09", 9, "oh-my-pi"),
      { ...cluster("2026-09-09", 9), failure_class: "other" },
    ]),
    ...RANGE,
  );
  expect(result.pre_matched).toBe(2);
  expect(result.pre_rate).toBe(2 / 20);
});

test("a dormant repeated-correction finding keeps its kind for its subject label", () => {
  const data = interventionsData(
    { task_daily: tasks },
    {
      findings: [
        {
          detector: "facts.repeated_correction",
          state: "dormant",
          harnesses: ["codex_exec"],
        },
      ],
    },
    { harness: "", start: RANGE[0], end: RANGE[1] },
  );
  expect(data.findings![0]!.kind).toBe("correction");
  expect(data.corrections).toBeUndefined();
  expect(data.kpis![0]!.corrections).toBeUndefined();
});

const workflow = {
  findings: [
    {
      detector: "facts.failure_cluster",
      state: "actionable",
      harnesses: ["codex_exec"],
    },
    {
      detector: "facts.repeated_correction",
      state: "emerging",
      harnesses: ["claude-code"],
    },
    { detector: "facts.failure_cluster", state: "emerging", harnesses: null },
  ],
  proposals: [
    { ...proposal, id: "p1", verdict: "validated" },
    {
      ...proposal,
      id: "p2",
      state: "pending",
      applied_at: null,
      tier: null,
      harnesses: ["claude-code"],
    },
    {
      ...proposal,
      id: "p3",
      verdict: null,
      // outside the range: applied counts, in range does not.
      applied_at: "2026-08-01T00:00:00Z",
      metric_harnesses: ["codex_exec"],
      harnesses: ["claude-code"],
    },
  ],
  proposal_events: [
    { proposal_id: "p1", event: "applied" },
    { proposal_id: "p2", event: "applied" },
  ],
};
const clickhouse = {
  cluster_daily: [cluster("2026-09-09", 2)],
  task_daily: tasks,
  baseline_daily: [{ day: "2026-09-09", failures_rate: 5 }],
};
const all = { harness: "", start: RANGE[0], end: RANGE[1] };

test("All scope computes KPI counts, verdict parts, tiers, history and comparisons", () => {
  const data = interventionsData(clickhouse, workflow, all);
  expect(data.kpis).toEqual([
    {
      findings: 3,
      actionable: 1,
      proposals: 3,
      pending: 1,
      applied: 2,
      applied_in_range: 1,
      validated: 1,
      regressed: 0,
      inconclusive: 0,
      not_evaluated: 1,
    },
  ]);
  expect(data.tiers).toEqual([
    { tier: "established_tool", proposals: 2 },
    { tier: "not recorded", proposals: 1 },
  ]);
  expect(data.proposal_events).toHaveLength(2);
  expect(data.comparisons).toHaveLength(1);
  expect(data.comparisons![0]!.pre_matched).toBe(2);
  expect(data.baseline_daily).toEqual(clickhouse.baseline_daily);
  expect(data.cluster_daily).toBeUndefined();
});

test("a harness scope keeps rows seen in it by metric harnesses first; rows without a list drop out", () => {
  const data = interventionsData(clickhouse, workflow, {
    ...all,
    harness: "codex_exec",
  });
  expect(data.findings).toHaveLength(1);
  // p1 (harnesses) and p3 (metric_harnesses override harnesses) stay; p2 does not.
  expect(data.proposals!.map((row) => row.id)).toEqual(["p1", "p3"]);
  expect(data.proposal_events).toEqual([
    { proposal_id: "p1", event: "applied" },
  ]);
  expect(data.kpis![0]).toMatchObject({
    findings: 1,
    proposals: 2,
    applied: 2,
  });
});

test("the baseline query computes per-day rates from summed numerators and denominators", () => {
  const sql = VIEW_QUERIES.interventions.baseline_daily!;
  expect(sql).toContain(
    "if(sum(t_n) > 0, 100 * sum(f_n) / sum(t_n), NULL) AS failures_rate",
  );
  expect(sql).toContain(
    "if(sum(l_n) > 0, 100 * sum(c_n) / sum(l_n), NULL) AS corrected_rate",
  );
  expect(sql).toContain("HAVING tasks > 0 OR labelled > 0");
});
