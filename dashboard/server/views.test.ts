import { describe, expect, test } from "bun:test";
import { VIEW_QUERIES } from "./views";

describe("Batch C view queries", () => {
  test("verification queries read only rows that carry the command shape", () => {
    for (const key of [
      "verification_kpi",
      "verification_kinds",
      "verification_caught",
      "verification_projects",
      "gate_runs",
    ])
      expect(VIEW_QUERIES.guardrails[key]).toContain(
        "shape_v2 = 1 AND tool_family = 'shell'",
      );
  });

  test("policy-blocked calls are neither explicit outcomes nor failures", () => {
    const all = Object.values(VIEW_QUERIES).flatMap((queries) =>
      Object.values(queries),
    );
    for (const sql of all) expect(sql).not.toContain("policy_blocked");
    expect(VIEW_QUERIES.tools.kpi).toContain(
      "outcome IN ('succeeded', 'failed', 'aborted')",
    );
  });

  test("kind rows order by a fixed kind order, then harness order, never by value", () => {
    expect(VIEW_QUERIES.guardrails.verification_kinds).toMatch(
      /ORDER BY if\(indexOf\(\['unit_test'.*kind\).*indexOf\(\['oh-my-pi'/s,
    );
    expect(VIEW_QUERIES.effort.intervals).toContain(
      "quantilesExact(0.25, 0.5, 0.75)",
    );
  });
});
