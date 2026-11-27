import type { Row } from "../src/contracts";

export const HOME_PATH =
  "'/(Users|home)/[A-Za-z0-9._-]+|/root([^A-Za-z0-9._-]|$)'";
export const HOME_PATH_CUTOFF = "2026-10-13 00:00:00";
export const AFTER_CUTOFF = `loaded_at >= toDateTime64('${HOME_PATH_CUTOFF}', 3, 'UTC')`;

const RAW_FAMILIES = {
  hooks: ["agent_introspection.hook_event", "agent-introspection.hook-event/1"],
  spool: [
    "agent_introspection.spool_status",
    "agent-introspection.spool-status/1",
  ],
} as const;

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

export const spoolExceptions = (rows: Row[]): Row[] =>
  rows.filter(
    (row) =>
      row["state"] === "unknown" ||
      Number(row["delivery_gap"]) === 1 ||
      Number(row["rejected"]) > 0 ||
      String(row["last_error_class"] ?? "") !== "",
  );

export const spoolSummary = (rows: Row[]): Row => {
  const unknown = rows.filter((row) => row["state"] === "unknown").length;
  return { reporting: rows.length - unknown, unknown };
};

const SPOOL_STALE_SECONDS = 300;

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

const DELIVERY_LAG_MINUTES = 5;
const DELIVERY_WINDOW = "24 HOUR";

export const ORDERED_TOTALS =
  "arrayMap(beat -> beat.2, arraySort(groupArray((produced_ns, sent_total))))";

export const SENT_FROM_TOTALS = (totals: string): string =>
  `arraySum(arrayMap((cur, prev) -> if(cur >= prev, cur - prev, cur), arrayPopFront(${totals}), arrayPopBack(${totals})))`;

export const PRODUCER_PRUNE = "4 DAY";

export const LOGS_TABLE = "signoz_logs.distributed_logs_v2";

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

export const deliveryArrived = (source: string): string => `arrived AS (
    SELECT resources_string['host.name'] AS host,
        'hook-event' AS family,
        observed_timestamp AS observed_ns
    FROM ${source}
    WHERE timestamp > ${NS_AGO(PRODUCER_PRUNE)} AND observed_timestamp > ${NS_AGO("25 HOUR")}
        AND ${rawFamily("hooks")}
)`;

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

const GAP_FLOOR = 2;
const GAP_FRACTION = 0.02;

const DELIVERY_COLUMNS = {
  "hook-event": ["hooks_sent", "hooks_received"],
} as const;

const hasGap = (entry: Row | undefined): boolean =>
  entry !== undefined &&
  Number(entry["sent"]) - Number(entry["received"]) >
    Math.max(GAP_FLOOR, Math.ceil(Number(entry["sent"]) * GAP_FRACTION));

export const withDelivery = (status: Row[], delivery: Row[]): Row[] =>
  status.map((row) => hostDelivery(row, delivery));

const hostDelivery = (row: Row, delivery: Row[]): Row => {
  const merged: Row = { ...row, delivery_gap: 0 };
  const mine = delivery.filter((entry) => entry["host"] === row["host"]);
  const entries: [readonly string[], Row | undefined][] = [];
  for (const [family, keys] of Object.entries(DELIVERY_COLUMNS))
    entries.push([keys, mine.find((entry) => entry["family"] === family)]);
  for (const [[sentKey, receivedKey], entry] of entries) {
    merged[sentKey!] = entry ? Number(entry["sent"]) : null;
    merged[receivedKey!] = entry ? Number(entry["received"]) : null;
    if (hasGap(entry)) merged["delivery_gap"] = 1;
  }
  return merged;
};
