/**
 * SQL for the records every machine sends over the one OTLP route (hook
 * events, session attribution, spool status), plus the cross-machine home-path
 * rule the sanitization checks share.
 */
import type { Row } from "../src/contracts";

/**
 * Home paths of any machine: `/Users/<name>`, `/home/<name>` or `/root`; the
 * bare directory names nobody. Single-quoted SQL regex.
 */
export const HOME_PATH =
  "'/(Users|home)/[A-Za-z0-9._-]+|/root([^A-Za-z0-9._-]|$)'";
/**
 * Deploy cutoff of the one-route hook ingest (UTC). Linux-home rows loaded
 * before it hold unredacted `/home/<name>` paths and stay on this machine
 * unmodified (M4 option b), so home-path counts only cover rows loaded at or
 * after it: the baseline for the home_paths check.
 */
export const HOME_PATH_CUTOFF = "2026-10-13 00:00:00";
export const AFTER_CUTOFF = `loaded_at >= toDateTime64('${HOME_PATH_CUTOFF}', 3, 'UTC')`;

/** The record families the agent-introspection service sends (contract: event.name + schema). */
const RAW_FAMILIES = {
  hooks: ["agent_introspection.hook_event", "agent-introspection.hook-event/1"],
  attribution: [
    "agent_introspection.session_project",
    "agent-introspection.session-project/1",
  ],
  spool: [
    "agent_introspection.spool_status",
    "agent-introspection.spool-status/1",
  ],
} as const;

/**
 * The mandatory source predicate: the service alone also holds legacy rows
 * with other event names, so every read names the event and the schema.
 */
export const rawFamily = (family: keyof typeof RAW_FAMILIES): string => {
  const [event, schema] = RAW_FAMILIES[family];
  return `resources_string['service.name'] = 'agent-introspection' AND attributes_string['event.name'] = '${event}' AND JSONExtractString(body, 'schema') = '${schema}'`;
};
export const RAW_ANY_FAMILY = `(${(
  Object.keys(RAW_FAMILIES) as (keyof typeof RAW_FAMILIES)[]
)
  .map((family) => `(${rawFamily(family)})`)
  .join(" OR ")})`;
export const NS_AGO = (interval: string) =>
  `toUInt64(toUnixTimestamp(now() - INTERVAL ${interval})) * 1000000000`;

/** Hosts that cannot be trusted or need a look: unknown, rejected files or a failing drain. */
export const spoolExceptions = (rows: Row[]): Row[] =>
  rows.filter(
    (row) =>
      row.state === "unknown" ||
      Number(row.rejected) > 0 ||
      String(row.last_error_class ?? "") !== "",
  );

/** Hosts reporting and hosts unknown: the one-line summary of the spool panel. */
export const spoolSummary = (rows: Row[]): Row => {
  const unknown = rows.filter((row) => row.state === "unknown").length;
  return { reporting: rows.length - unknown, unknown };
};

/** A host whose latest spool status is older than this reports nothing about its backlog. */
const SPOOL_STALE_SECONDS = 300;

/**
 * The latest spool_status heartbeat per host. A stale host's backlog is NULL
 * and its state is `unknown`: an old report never reads as a backlog of 0.
 */
export const SPOOL_STATUS = `SELECT host, toString(reported) AS reported_at, age_seconds,
    if(age_seconds > ${SPOOL_STALE_SECONDS}, 'unknown', 'reporting') AS state,
    if(state = 'unknown', NULL, JSONExtract(latest, 'pending', 'Nullable(Int64)')) AS pending,
    if(state = 'unknown', NULL, JSONExtract(latest, 'rejected', 'Nullable(Int64)')) AS rejected,
    if(state = 'unknown', NULL, JSONExtract(latest, 'oldest_pending_seconds', 'Nullable(Int64)')) AS oldest_pending_seconds,
    JSONExtractString(latest, 'last_error_class') AS last_error_class,
    JSONExtractString(latest, 'cli_version') AS cli_version
FROM (
    -- \`latest\`, not \`body\`: the alias would shadow the column the family filter reads.
    SELECT resources_string['host.name'] AS host, argMax(body, timestamp) AS latest,
        fromUnixTimestamp64Nano(toInt64(max(timestamp)), 'UTC') AS reported,
        dateDiff('second', reported, now()) AS age_seconds
    FROM signoz_logs.distributed_logs_v2
    WHERE timestamp > ${NS_AGO("30 DAY")} AND ${rawFamily("spool")}
    GROUP BY host
)
ORDER BY host`;
