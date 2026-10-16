/**
 * SQL for the records every machine sends over the one OTLP route (hook
 * events and spool status), plus the cross-machine home-path
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

/**
 * A host whose latest spool status was received longer ago than this reports
 * nothing about its backlog. Ageing uses \`observed_timestamp\`, which the
 * collector stamps, never the producer's own \`timestamp\`: a skewed host clock
 * must not make a silent host look fresh.
 */
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
    SELECT resources_string['host.name'] AS host, argMax(body, observed_timestamp) AS latest,
        fromUnixTimestamp64Nano(toInt64(max(observed_timestamp)), 'UTC') AS reported,
        dateDiff('second', reported, now()) AS age_seconds
    FROM signoz_logs.distributed_logs_v2
    WHERE timestamp > ${NS_AGO("31 DAY")} AND observed_timestamp > ${NS_AGO("30 DAY")}
        AND ${rawFamily("spool")}
    GROUP BY host
)
ORDER BY host`;

/** Heartbeats newer than this are left out of the sent window: their records may still be arriving. */
const DELIVERY_LAG_MINUTES = 5;
/** Window of the sent-versus-received comparison. */
const DELIVERY_WINDOW = "24 HOUR";

/**
 * A group's cumulative counter values in the producer's order (`produced_ns`,
 * `sent_total` columns). \`produced_ns\` is the heartbeat's own \`timestamp\`
 * (reported_at), monotonic per host, so a stale heartbeat shipped late cannot
 * read as a counter reset the way collector arrival order would.
 */
export const ORDERED_TOTALS =
  "arrayMap(beat -> beat.2, arraySort(groupArray((produced_ns, sent_total))))";

/** Sent records from `totals`, an array of cumulative counter values in producer order. */
export const SENT_FROM_TOTALS = (totals: string): string =>
  `arraySum(arrayMap((cur, prev) -> if(cur >= prev, cur - prev, cur), arrayPopFront(${totals}), arrayPopBack(${totals})))`;

/**
 * Partition-pruning guard on the producer \`timestamp\` of delivery sources. It
 * only prunes; it must never be tighter than the loaders' sweep horizon (3 days
 * on \`observed_timestamp\`, facts.py), or records the sender counted as sent
 * and the loaders still pick up would drop out of received. Hence 4 days.
 */
export const PRODUCER_PRUNE = "4 DAY";

/** The logs table the delivery check reads. */
export const LOGS_TABLE = "signoz_logs.distributed_logs_v2";

/**
 * Per host and family, the heartbeats' cumulative counters in producer order
 * and the window bounds (collector \`observed_timestamp\`) of the delivery
 * window, ending a few minutes ago. \`source\`
 * is any table or subquery with the logs columns.
 */
export const deliveryBeats = (source: string): string => `beats AS (
    SELECT host, family,
        ${ORDERED_TOTALS} AS totals,
        min(observed_ns) AS first_ns, max(observed_ns) AS last_ns
    FROM (
        SELECT resources_string['host.name'] AS host, observed_timestamp AS observed_ns,
            timestamp AS produced_ns, family,
            JSONExtractInt(body, 'sent_total', family) AS sent_total
        FROM ${source}
        ARRAY JOIN ['hook-event'] AS family
        WHERE timestamp > ${NS_AGO(PRODUCER_PRUNE)}
            AND observed_timestamp > ${NS_AGO(DELIVERY_WINDOW)} AND observed_timestamp <= ${NS_AGO(`${DELIVERY_LAG_MINUTES} MINUTE`)}
            AND ${rawFamily("spool")}
    )
    GROUP BY host, family
)`;

/** Per host and family, the arrival time of each record the host's loaders can see. */
export const deliveryArrived = (source: string): string => `arrived AS (
    SELECT resources_string['host.name'] AS host,
        'hook-event' AS family,
        observed_timestamp AS observed_ns
    FROM ${source}
    WHERE timestamp > ${NS_AGO(PRODUCER_PRUNE)} AND observed_timestamp > ${NS_AGO("25 HOUR")}
        AND ${rawFamily("hooks")}
)`;

/**
 * Per host and record family: records the host's spool reports sending in the
 * window against records SigNoz holds for that host, to find batches the
 * collector accepted and then lost.
 *
 * Sent is the growth of the cumulative \`sent_total\` counter across the window's
 * heartbeats in producer order (each heartbeat's own \`timestamp\`, monotonic per
 * host, so a late-shipped stale heartbeat is not a reset): the sum of positive
 * increments between consecutive heartbeats, where a decrease is a counter
 * reset and the post-reset value itself is the increment (100, 0, 150 sent 150).
 * The window bounds stay on collector-stamped \`observed_timestamp\`, which a
 * skewed host clock cannot move. Heartbeats from the last ${DELIVERY_LAG_MINUTES}
 * minutes are excluded so in-flight batches never read as lost. Received counts
 * the family's records by arrival (\`observed_timestamp\`, the loaders' basis)
 * between the first and last of those heartbeats.
 */
export const spoolDelivery = (
  source: string,
): string => `WITH ${deliveryBeats(source)},
${deliveryArrived(source)}
SELECT b.host AS host, b.family AS family,
    ${SENT_FROM_TOTALS("b.totals")} AS sent,
    countIf(a.observed_ns > b.first_ns AND a.observed_ns <= b.last_ns) AS received
FROM beats AS b
LEFT JOIN arrived AS a ON a.host = b.host AND a.family = b.family
GROUP BY b.host, b.family, b.totals, b.first_ns, b.last_ns
ORDER BY host, family`;

export const SPOOL_DELIVERY = spoolDelivery(LOGS_TABLE);

/** Records short of sent that still read as in flight or load lag, not loss. */
const GAP_FLOOR = 2;
const GAP_FRACTION = 0.02;

const DELIVERY_COLUMNS = {
  "hook-event": ["hooks_sent", "hooks_received"],
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
