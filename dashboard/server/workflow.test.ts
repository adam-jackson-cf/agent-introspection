import { Database } from "bun:sqlite";
import { describe, expect, test } from "bun:test";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { sqliteWorkflow } from "./workflow";

const scratch = () =>
  join(
    tmpdir(),
    `workflow-${process.pid}-${Math.random().toString(36).slice(2)}.sqlite3`,
  );

describe("sqliteWorkflow", () => {
  test("an absent store reads as empty", () => {
    expect(sqliteWorkflow(scratch())()).toEqual({
      findings: [],
      proposals: [],
      proposal_events: [],
    });
  });

  test("a store without the subject column reads with null subjects", () => {
    const path = scratch();
    const db = new Database(path);
    db.run(`CREATE TABLE findings (category TEXT, detector_id TEXT, trend_state TEXT,
      occurrence_count INTEGER, canonical_task_count INTEGER, local_day_count INTEGER,
      first_seen_ns INTEGER, last_seen_ns INTEGER, is_active INTEGER, id TEXT)`);
    db.run(`CREATE TABLE proposals (id TEXT, finding_id TEXT, state TEXT, payload_json TEXT,
      created_at TEXT, updated_at TEXT)`);
    db.run(
      `INSERT INTO findings VALUES ('tool', 'd', 'actionable', 3, 2, 2, 0, 0, 1, 'f1')`,
    );
    db.close();

    const findings = sqliteWorkflow(path)()["findings"] ?? [];
    rmSync(path);

    expect(findings).toHaveLength(1);
    expect(findings[0]!["harnesses"]).toBeNull();
    expect(findings[0]!["tool_family"]).toBeNull();
    expect(findings[0]!["state"]).toBe("actionable");
  });

  test("reads structured subjects, success metrics, and the latest evaluation", () => {
    const path = scratch();
    const db = new Database(path);
    db.run(`CREATE TABLE findings (id TEXT, category TEXT, detector_id TEXT, trend_state TEXT,
      occurrence_count INTEGER, canonical_task_count INTEGER, local_day_count INTEGER,
      first_seen_ns INTEGER, last_seen_ns INTEGER, is_active INTEGER, subject TEXT)`);
    db.run(`CREATE TABLE proposals (id TEXT, finding_id TEXT, state TEXT, payload_json TEXT,
      created_at TEXT, updated_at TEXT)`);
    db.run(`CREATE TABLE proposal_events (id TEXT, proposal_id TEXT, sequence INTEGER,
      event_type TEXT, payload_json TEXT, created_at TEXT)`);
    const cluster = JSON.stringify({
      tool_family: "shell",
      failure_class: "cmd not found",
      harnesses: ["claude-code", "oh-my-pi"],
      tools: ["Bash", "bash"],
      examples: ["x"],
      impact: 5,
    });
    const correction = JSON.stringify({
      project: "repo",
      correction_kind: "wrong_scope",
      harnesses: ["codex_cli_rs"],
      task_types: ["feature"],
      sessions: 2,
      impact: 3,
    });
    const insert = db.prepare(
      "INSERT INTO findings VALUES (?, ?, ?, 'actionable', 3, 2, 2, 0, 0, 1, ?)",
    );
    insert.run("f1", "tool_failure_cluster", "facts.failure_cluster", cluster);
    insert.run(
      "f2",
      "repeated_correction",
      "facts.repeated_correction",
      correction,
    );
    const metric = {
      metric: "cluster_task_rate",
      harnesses: ["claude-code"],
      baseline_days: 14,
      evaluation_days: 7,
      max_ratio: 0.5,
    };
    db.run(
      "INSERT INTO proposals VALUES ('p1', 'f1', 'applied', ?, '2026-09-01T00:00:00Z', '2026-09-03T00:00:00Z')",
      [
        JSON.stringify({
          intervention_type: "script",
          target: "t",
          predicted_success_metric: metric,
        }),
      ],
    );
    const event = db.prepare(
      "INSERT INTO proposal_events VALUES (?, 'p1', ?, ?, ?, ?)",
    );
    event.run("e1", 1, "applied", "{}", "2026-09-02T10:00:00Z");
    event.run(
      "e2",
      2,
      "evaluated",
      JSON.stringify({
        verdict: "regressed",
        ratio: 1.5,
        baseline_rate: 0.1,
        evaluation_rate: 0.15,
      }),
      "2026-09-20T00:00:00Z",
    );
    event.run(
      "e3",
      3,
      "evaluated",
      JSON.stringify({
        verdict: "validated",
        ratio: 0.25,
        baseline_rate: 0.2,
        evaluation_rate: 0.05,
      }),
      "2026-09-21T00:00:00Z",
    );
    event.run(
      "e4",
      4,
      "decided",
      JSON.stringify({
        actor: "adam",
        evidence: { reason: "ok to ship", prompt: "secret-prompt" },
      }),
      "2026-09-22T00:00:00Z",
    );
    db.close();

    const workflow = sqliteWorkflow(path)();
    const findings = workflow["findings"] ?? [];
    const proposals = workflow["proposals"] ?? [];
    const proposal_events = workflow["proposal_events"] ?? [];
    rmSync(path);
    expect(proposal_events.map((row) => row["event"])).toEqual([
      "decided",
      "evaluated",
      "evaluated",
      "applied",
    ]);
    expect(proposal_events[0]).toMatchObject({
      proposal_id: "p1",
      actor: "adam",
      summary: "ok to ship",
      at: "2026-09-22T00:00:00Z",
    });
    expect(proposal_events[1]!["summary"]).toBe("validated");
    expect(JSON.stringify(proposal_events)).not.toContain("secret-prompt");

    expect(
      findings.find((row) => row["detector"] === "facts.failure_cluster"),
    ).toMatchObject({
      tool_family: "shell",
      failure_class: "cmd not found",
      harnesses: ["claude-code", "oh-my-pi"],
      tools: ["Bash", "bash"],
      impact: 5,
    });
    expect(
      findings.find((row) => row["detector"] === "facts.repeated_correction"),
    ).toMatchObject({
      project: "repo",
      correction_kind: "wrong_scope",
      harnesses: ["codex_cli_rs"],
      task_types: ["feature"],
      sessions: 2,
    });
    expect(proposals[0]).toMatchObject({
      tier: "script",
      metric: "cluster_task_rate",
      metric_harnesses: ["claude-code"],
      max_ratio: 0.5,
      applied_at: "2026-09-02T10:00:00Z",
      verdict: "validated",
      evaluated_ratio: 0.25,
      baseline_rate: 0.2,
      evaluation_rate: 0.05,
      tool_family: "shell",
    });
  });
});
