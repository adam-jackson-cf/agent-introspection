import type { Row, ViewResponse } from "../src/contracts";

export const REFRESHERS = `SELECT view, toString(last_success_time) AS as_of,
    toUInt8(exception != '' OR now() > next_refresh_time + INTERVAL 2 MINUTE) AS stale
FROM system.view_refreshes WHERE database = 'introspection'`;

const LOADERS = ["load_spans", "sweep_spans", "load_logs", "sweep_logs"];
const RECORD_LOADERS: Array<[RegExp, string[]]> = [
  [/introspection\.hook_rows\b/, ["load_hook_events", "sweep_hook_events"]],
];
const HISTORY = /_history$/;
const SWEEP = /^sweep_/;

export function refreshersFor(
  queries: Record<string, string>,
  snapshots: boolean,
): string[] {
  const names = new Set(LOADERS);
  const sqls = Object.values(queries);
  for (const [table, loaders] of RECORD_LOADERS)
    if (sqls.some((sql) => table.test(sql)))
      for (const loader of loaders) names.add(loader);
  if (!snapshots) return [...names];
  const views = sqls.flatMap((sql) =>
    [...sql.matchAll(/introspection\.(\w+)_snapshot\b/g)].map(
      ([, view]) => view,
    ),
  );
  for (const view of views) {
    names.add(`refresh_${view}_history`);
    names.add(`refresh_${view}_recent`);
  }
  return [...names];
}

export function asOfFor(
  rows: Row[],
  scope: string[] | null,
): ViewResponse["asOf"] {
  const inScope = rows.filter(
    (row) => scope === null || scope.includes(String(row["view"])),
  );
  const times = inScope
    .filter(
      (row) =>
        !HISTORY.test(String(row["view"])) && !SWEEP.test(String(row["view"])),
    )
    .map((row) => row["as_of"])
    .filter(
      (at): at is string =>
        typeof at === "string" && at !== "" && !at.startsWith("1970"),
    );
  if (times.length === 0) return null;
  const failing = inScope.filter((row) => Number(row["stale"]) === 1);
  const named = (history: boolean) =>
    failing.some((row) => HISTORY.test(String(row["view"])) === history);
  return {
    at: times.reduce((a, b) => (a < b ? a : b)).replace(" ", "T") + "Z",
    stale: named(false),
    historyStale: named(true),
    failing: failing.map((row) => String(row["view"])).toSorted(),
  };
}
