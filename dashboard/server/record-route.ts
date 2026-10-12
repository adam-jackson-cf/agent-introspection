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

/** Hosts that cannot be trusted or need a look: unknown, rejected files, a failing drain or sent records that never arrived. */
export const spoolExceptions = (rows: Row[]): Row[] =>
  rows.filter(
    (row) =>
      row.state === "unknown" ||
      Number(row.delivery_gap) === 1 ||
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

/** Heartbeats newer than this are left out of the sent window: their records may still be arriving. */
const DELIVERY_LAG_MINUTES = 5;
/** Window of the sent-versus-received comparison. */
const DELIVERY_WINDOW = "24 HOUR";

/**
 * Per host and record family: records the host's spool reports sending in the
 * window against records SigNoz holds for that host, to find batches the
 * collector accepted and then lost.
 *
 * Sent is the cumulative \`sent_total\` counter's growth between the earliest and
 * latest heartbeat in the window; a counter that went down was reset, so its
 * latest value is the sent count. Heartbeats from the last
 * ${DELIVERY_LAG_MINUTES} minutes are excluded so in-flight batches never
 * read as lost. Received counts the family's records by arrival
 * (\`observed_timestamp\`, the loaders' basis) between those same two heartbeats.
 */
export const SPOOL_DELIVERY = `WITH beats AS (
    SELECT host, family, argMin(sent_total, ts) AS first_total, argMax(sent_total, ts) AS last_total,
        min(ts) AS first_ns, max(ts) AS last_ns
    FROM (
        SELECT resources_string['host.name'] AS host, timestamp AS ts, family,
            JSONExtractInt(body, 'sent_total', family) AS sent_total
        FROM signoz_logs.distributed_logs_v2
        ARRAY JOIN ['hook-event', 'session-project'] AS family
        WHERE timestamp > ${NS_AGO(DELIVERY_WINDOW)} AND timestamp <= ${NS_AGO(`${DELIVERY_LAG_MINUTES} MINUTE`)}
            AND ${rawFamily("spool")}
    )
    GROUP BY host, family
),
arrived AS (
    SELECT resources_string['host.name'] AS host,
        if(attributes_string['event.name'] = 'agent_introspection.hook_event', 'hook-event', 'session-project') AS family,
        observed_timestamp AS observed_ns
    FROM signoz_logs.distributed_logs_v2
    WHERE timestamp > ${NS_AGO("25 HOUR")} AND ((${rawFamily("hooks")}) OR (${rawFamily("attribution")}))
)
SELECT b.host AS host, b.family AS family,
    if(b.last_total < b.first_total, b.last_total, b.last_total - b.first_total) AS sent,
    countIf(a.observed_ns > b.first_ns AND a.observed_ns <= b.last_ns) AS received
FROM beats AS b
LEFT JOIN arrived AS a ON a.host = b.host AND a.family = b.family
GROUP BY b.host, b.family, b.first_total, b.last_total
ORDER BY host, family`;

/** Records short of sent that still read as in flight or load lag, not loss. */
const GAP_FLOOR = 2;
const GAP_FRACTION = 0.02;

const DELIVERY_COLUMNS = {
  "hook-event": ["hooks_sent", "hooks_received"],
  "session-project": ["attribution_sent", "attribution_received"],
} as const;

/** Whether a family received fewer records than it sent beyond the tolerance. */
const hasGap = (entry: Row | undefined): boolean =>
  entry !== undefined &&
  Number(entry.sent) - Number(entry.received) >
    Math.max(GAP_FLOOR, Math.ceil(Number(entry.sent) * GAP_FRACTION));

/**
 * Adds each host's sent and received counts per family to its spool status row,
 * with \`delivery_gap = 1\` when any family received fewer records than sent
 * beyond the tolerance. A host with no windowed heartbeat gets nulls and no flag.
 */
export const withDelivery = (status: Row[], delivery: Row[]): Row[] =>
  status.map((row) => hostDelivery(row, delivery));

const hostDelivery = (row: Row, delivery: Row[]): Row => {
  const merged: Row = { ...row, delivery_gap: 0 };
  const mine = delivery.filter((entry) => entry.host === row.host);
  const entries: [readonly string[], Row | undefined][] = [];
  for (const [family, keys] of Object.entries(DELIVERY_COLUMNS))
    entries.push([keys, mine.find((entry) => entry.family === family)]);
  for (const [[sentKey, receivedKey], entry] of entries) {
    merged[sentKey!] = entry ? Number(entry.sent) : null;
    merged[receivedKey!] = entry ? Number(entry.received) : null;
    if (hasGap(entry)) merged.delivery_gap = 1;
  }
  return merged;
};
