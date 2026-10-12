import type { Row, ViewResponse } from "../src/contracts";

/** One row per fact refresher (loader or snapshot refresh): name, last success, staleness. */
export const REFRESHERS = `SELECT view, toString(last_success_time) AS as_of,
    toUInt8(exception != '' OR now() > next_refresh_time + INTERVAL 2 MINUTE) AS stale
FROM system.view_refreshes WHERE database = 'introspection'`;

/** The minute loaders every fact view is built from. */
const LOADERS = ["load_spans", "load_logs"];
const HISTORY = /_history$/;

/**
 * The refreshers whose output a view's queries read: the minute loaders, plus the
 * history and recent refreshers of each snapshot the SQL names (facts.py
 * `snapshot_refreshers`). Windows older than the snapshots read the live views,
 * which only the loaders feed.
 */
export function refreshersFor(
  queries: Record<string, string>,
  snapshots: boolean,
): string[] {
  const names = new Set(LOADERS);
  if (!snapshots) return [...names];
  const views = Object.values(queries).flatMap((sql) =>
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

/**
 * Freshness of the given refreshers (all of them when `scope` is null). `stale`
 * covers the loaders and recent refreshes, which move the newest days; the daily
 * history refreshes only age older days, so their failure is `historyStale`.
 * `failing` names every stale refresher in scope.
 */
export function asOfFor(
  rows: Row[],
  scope: string[] | null,
): ViewResponse["asOf"] {
  const inScope = rows.filter(
    (row) => scope === null || scope.includes(String(row.view)),
  );
  // A refresher that has never succeeded reports no time (null) or the epoch.
  const times = inScope
    .map((row) => row.as_of)
    .filter(
      (at): at is string =>
        typeof at === "string" && at !== "" && !at.startsWith("1970"),
    );
  if (times.length === 0) return null;
  const failing = inScope.filter((row) => Number(row.stale) === 1);
  const named = (history: boolean) =>
    failing.some((row) => HISTORY.test(String(row.view)) === history);
  return {
    at: times.reduce((a, b) => (a > b ? a : b)).replace(" ", "T") + "Z",
    stale: named(false),
    historyStale: named(true),
    failing: failing.map((row) => String(row.view)).sort(),
  };
}
