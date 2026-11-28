import { Database } from "bun:sqlite";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { Row } from "../src/contracts";

const WORKFLOW_DB =
  process.env["INTROSPECTION_WORKFLOW_DB"] ??
  join(homedir(), ".local/share/agent-introspection/introspection.sqlite3");

export type WorkflowReader = () => Record<string, Row[]>;

const SUBJECT_SCALARS = [
  "tool_family",
  "failure_class",
  "project",
  "correction_kind",
  "impact",
  "sessions",
];
const SUBJECT_ARRAYS = ["harnesses", "tools", "task_types"];

const SUBJECT = (table: string, hasSubject: boolean) => {
  const field = (path: string) =>
    hasSubject
      ? `CASE WHEN ${table}.subject = '' THEN NULL ELSE json_extract(${table}.subject, '$.${path}') END`
      : "NULL";
  return [
    ...SUBJECT_SCALARS.map((name) => `${field(name)} AS ${name}`),
    ...SUBJECT_ARRAYS.filter((name) => name !== "harnesses").map(
      (name) => `${field(name)} AS ${name}`,
    ),
    hasSubject
      ? `CASE WHEN ${table}.subject = '' THEN NULL ELSE coalesce(json_extract(${table}.subject, '$.harnesses'),
          CASE WHEN json_extract(${table}.subject, '$.harness') IS NOT NULL
            THEN json_array(json_extract(${table}.subject, '$.harness')) END) END AS harnesses`
      : "NULL AS harnesses",
  ].join(", ");
};

const latestEvent = (hasEvents: boolean, type: string, expression: string) =>
  hasEvents
    ? `(SELECT ${expression} FROM proposal_events AS e WHERE e.proposal_id = p.id
        AND e.event_type = '${type}' ORDER BY e.sequence DESC LIMIT 1)`
    : "NULL";

const metricField = (path: string) =>
  `CASE WHEN json_type(p.payload_json, '$.predicted_success_metric') = 'object'
    THEN json_extract(p.payload_json, '$.predicted_success_metric.${path}') END`;

function parseArrays(row: Row, keys: string[]): Row {
  const out: Row = { ...row };
  for (const key of keys) {
    const value = row[key];
    if (typeof value !== "string") continue;
    try {
      const parsed: unknown = JSON.parse(value);
      out[key] = Array.isArray(parsed)
        ? parsed.map((item) => String(item))
        : null;
    } catch {
      out[key] = null;
    }
  }
  return out;
}

export function sqliteWorkflow(path = WORKFLOW_DB): WorkflowReader {
  return () => {
    if (!existsSync(path))
      return { findings: [], proposals: [], proposal_events: [] };
    const db = new Database(path, { readonly: true });
    try {
      const all = (sql: string) => db.query(sql).all() as Row[];
      const tables = new Set(
        all("SELECT name FROM sqlite_master WHERE type = 'table'").map((row) =>
          String(row["name"]),
        ),
      );
      if (!tables.has("findings") || !tables.has("proposals"))
        return { findings: [], proposals: [], proposal_events: [] };
      const hasSubject = all("PRAGMA table_info(findings)").some(
        (row) => row["name"] === "subject",
      );
      const hasEvents = tables.has("proposal_events");
      const findings =
        all(`SELECT category, detector_id AS detector, trend_state AS state,
            ${SUBJECT("findings", hasSubject)},
            occurrence_count AS occurrences, canonical_task_count AS tasks, local_day_count AS days,
            strftime('%Y-%m-%dT%H:%M:%SZ', first_seen_ns / 1e9, 'unixepoch') AS first_seen,
            strftime('%Y-%m-%dT%H:%M:%SZ', last_seen_ns / 1e9, 'unixepoch') AS last_seen
          FROM findings WHERE is_active = 1
          ORDER BY trend_state = 'actionable' DESC, occurrence_count DESC, last_seen_ns DESC`);
      const proposals =
        all(`SELECT p.id, p.state, json_extract(p.payload_json, '$.intervention_type') AS tier,
            json_extract(p.payload_json, '$.target') AS target, f.category, f.detector_id AS detector,
            p.created_at, ${SUBJECT("f", hasSubject)},
            ${metricField("metric")} AS metric,
            ${metricField("harnesses")} AS metric_harnesses,
            ${metricField("baseline_days")} AS baseline_days,
            ${metricField("evaluation_days")} AS evaluation_days,
            ${metricField("max_ratio")} AS max_ratio,
            coalesce(${latestEvent(hasEvents, "applied", "e.created_at")},
              CASE WHEN p.state = 'applied' THEN p.updated_at END) AS applied_at,
            ${latestEvent(hasEvents, "evaluated", "json_extract(e.payload_json, '$.verdict')")} AS verdict,
            ${latestEvent(hasEvents, "evaluated", "json_extract(e.payload_json, '$.ratio')")} AS evaluated_ratio,
            ${latestEvent(hasEvents, "evaluated", "json_extract(e.payload_json, '$.baseline_rate')")} AS baseline_rate,
            ${latestEvent(hasEvents, "evaluated", "json_extract(e.payload_json, '$.evaluation_rate')")} AS evaluation_rate
          FROM proposals AS p JOIN findings AS f ON f.id = p.finding_id ORDER BY p.created_at DESC`);
      const history = hasEvents
        ? all(`SELECT p.id AS proposal_id, json_extract(p.payload_json, '$.target') AS target,
            e.sequence, e.event_type AS event, json_extract(e.payload_json, '$.actor') AS actor,
            e.created_at AS at,
            substr(coalesce(json_extract(e.payload_json, '$.evidence.reason'),
              json_extract(e.payload_json, '$.evidence.decision'),
              json_extract(e.payload_json, '$.evidence.request'),
              json_extract(e.payload_json, '$.verdict')), 1, 200) AS summary
          FROM proposal_events AS e JOIN proposals AS p ON p.id = e.proposal_id
          ORDER BY e.created_at DESC, e.sequence DESC`)
        : [];
      return {
        findings: findings.map((row) => parseArrays(row, SUBJECT_ARRAYS)),
        proposals: proposals.map((row) =>
          parseArrays(row, [...SUBJECT_ARRAYS, "metric_harnesses"]),
        ),
        proposal_events: history,
      };
    } finally {
      db.close();
    }
  };
}
