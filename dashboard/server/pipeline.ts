import type {
  Contribution,
  ContributionState,
  Contributions,
  Harness,
  Registry,
  RouteSource,
  Row,
  SignalRoute,
} from "../src/contracts";
import { HARNESSES } from "../src/contracts";
import { enabledHarnesses, isHidden } from "../src/harness-scope";
import { batch, live, type Query } from "./clickhouse";
import { inWindow } from "./views";

type Params = Record<string, string>;
type Source = RouteSource;
type RouteCounts = {
  /** route → harness → rows matching the route predicate. */
  total: Map<string, Map<string, number>>;
  /** route → harness → matching rows not explained by a registered stray. */
  unexplained: Map<string, Map<string, number>>;
  /** route → harness → UTC day of the route's first row when no row precedes the window. */
  first: Map<string, Map<string, string>>;
};

const START = "{start:DateTime64(3, 'UTC')}";
const WINDOW = `ts >= ${START} AND ts < {end:DateTime64(3, 'UTC')}`;
const SOURCES: Source[] = ["spans", "logs", "hooks"];
/**
 * The relation each route source's predicates run against. Activity hooks are
 * read through `hook_rows`, a view that resolves each event's harness, so it
 * takes no FINAL (the view already reads `hook_events` FINAL).
 */
const SOURCE_RELATION: Record<Source, string> = {
  spans: "introspection.spans FINAL",
  logs: "introspection.logs FINAL",
  hooks: "introspection.hook_rows",
};
/** The event or span name column of each source, for unrouted-row reporting. */
const NAME_COLUMN: Record<Source, string> = {
  spans: "name",
  logs: "event_name",
  hooks: "event_type",
};
/** Codex spans the loader keeps; mirrors select_spans.sql, including its usage predicate. */
const CODEX_SPAN_NAMES =
  "'session_task.turn', 'session_task.run', 'run_sampling_request', 'try_run_sampling_request', 'turn/start', 'turn/interrupt', 'turn/steer'";
const CODEX_LOADED = `(name IN (${CODEX_SPAN_NAMES}) OR (name = 'handle_responses' AND mapContains(attributes_number, 'gen_ai.usage.input_tokens')))`;
const SERVICES =
  "'codex-app-server', 'codex_cli_rs', 'codex_exec', 'oh-my-pi', 'claude-code'";
const FORBIDDEN_KEYS =
  "['prompt', 'user_prompt', 'arguments', 'output', 'content', 'user.email', 'user.account_id', 'user.account_uuid', 'user.id', 'organization.id', 'gen_ai.tool.description', 'pi.gen_ai.tool.call.intent', 'omp.gen_ai.tool.call.intent']";
/** Raw-text keys the activity hooks must never store (docs/hook-events.md). */
const HOOK_FORBIDDEN_KEYS =
  "['prompt', 'user_prompt', 'tool_input', 'tool_response', 'arguments', 'output', 'content', 'message']";

const routePredicates = (registry: Registry, source: Source) => {
  const unique = new Map<string, string>();
  for (const route of registry.routes)
    if (route.source === source) unique.set(route.route, route.match);
  return [...unique];
};

const strayCondition = (registry: Registry, source: Source): string => {
  const strays = registry.strays.filter((stray) => stray.source === source);
  return strays.length === 0
    ? "0"
    : strays
        .map((stray) => `(harness = '${stray.harness}' AND (${stray.match}))`)
        .join(" OR ");
};

/**
 * Counts rows per harness for every route predicate over the window, and the
 * part of each count not explained by a registered stray. Predicates come from
 * the registry tables that `facts install` loaded from the repo-owned file. The
 * scan reaches back through all history before the window end, so each route also
 * reports the day of its first row when that row falls inside the window (a route
 * that started mid-window, such as an activity hook installed then).
 */
export function routeCountQueries(registry: Registry): Record<string, string> {
  const queries: Record<string, string> = {};
  for (const source of SOURCES) {
    const predicates = routePredicates(registry, source);
    if (predicates.length === 0) continue;
    const stray = strayCondition(registry, source);
    const columns = predicates.flatMap(([, match], index) => [
      `countIf((${match}) AND ts >= ${START}) AS t${index}`,
      `countIf((${match}) AND ts >= ${START} AND NOT (${stray})) AS u${index}`,
      `if(countIf((${match}) AND ts < ${START}) = 0, toString(toDate(minIf(ts, ${match}))), '') AS f${index}`,
    ]);
    queries[source] =
      `SELECT harness, ${columns.join(", ")} FROM ${SOURCE_RELATION[source]} WHERE ts < {end:DateTime64(3, 'UTC')} GROUP BY harness`;
  }
  return queries;
}

function harnessCounts(rows: Row[], index: number) {
  const totals = new Map<string, number>();
  const rest = new Map<string, number>();
  const first = new Map<string, string>();
  for (const row of rows) {
    totals.set(String(row.harness), Number(row[`t${index}`]));
    rest.set(String(row.harness), Number(row[`u${index}`]));
    const day = String(row[`f${index}`] ?? "");
    if (Number(row[`t${index}`]) > 0 && day !== "")
      first.set(String(row.harness), day);
  }
  return [totals, rest, first] as const;
}

/** Interprets the per-source count rows of `routeCountQueries`. */
export function countsFromRows(
  registry: Registry,
  rows: Record<string, Row[]>,
): RouteCounts {
  const total = new Map<string, Map<string, number>>();
  const unexplained = new Map<string, Map<string, number>>();
  const first = new Map<string, Map<string, string>>();
  for (const source of SOURCES)
    routePredicates(registry, source).forEach(([route], index) => {
      const [totals, rest, days] = harnessCounts(rows[source] ?? [], index);
      total.set(route, totals);
      unexplained.set(route, rest);
      first.set(route, days);
    });
  return { total, unexplained, first };
}

export async function routeCounts(
  query: Query,
  registry: Registry,
  params: Params,
): Promise<RouteCounts> {
  return countsFromRows(
    registry,
    await batch(query, routeCountQueries(registry), params),
  );
}

const routeOf = (registry: Registry, signal: string, harness: string) =>
  registry.support.find(
    (entry) => entry.signal === signal && entry.harness === harness,
  );

export type CoverageState =
  | "healthy"
  | "idle"
  | "possible break"
  | "no events"
  | "not applicable"
  | "stray (explained)"
  | "stray (unexplained)"
  | "not in use"
  | "not in use, rows present";

const harnessesWithRows = (registry: Registry, counts: RouteCounts) => {
  const active = new Set<string>();
  for (const route of registry.routes)
    if ((counts.total.get(route.route)?.get(route.harness) ?? 0) > 0)
      active.add(route.harness);
  return active;
};

const hasRoute = (registry: Registry, route: string, harness: Harness) =>
  registry.routes.some(
    (candidate: SignalRoute) =>
      candidate.route === route && candidate.harness === harness,
  );

/** Rows and unexplained rows on the signal's routes that are not this harness's own. */
function foreignCounts(
  registry: Registry,
  counts: RouteCounts,
  signalRoutes: Set<string>,
  own: { route: string; harness: Harness },
): { foreign: number; unexplained: number } {
  let foreign = 0;
  let unexplained = 0;
  const others = [...signalRoutes].filter(
    (route) => route !== own.route && !hasRoute(registry, route, own.harness),
  );
  for (const route of others) {
    foreign += counts.total.get(route)?.get(own.harness) ?? 0;
    unexplained += counts.unexplained.get(route)?.get(own.harness) ?? 0;
  }
  return { foreign, unexplained };
}

type CellCounts = { own: number; foreign: number; unexplained: number };

function notApplicableState(cell: CellCounts): CoverageState {
  if (cell.unexplained > 0 || cell.own > 0) return "stray (unexplained)";
  return cell.foreign > 0 ? "stray (explained)" : "not applicable";
}

function silentState(
  registry: Registry,
  support: Registry["support"][number],
  harness: Harness,
  active: Set<string>,
): CoverageState {
  if (!active.has(harness)) return "idle";
  const expectsEvents = registry.routes.some(
    (route) =>
      route.route === support.route &&
      route.harness === harness &&
      route.expect === "events",
  );
  return expectsEvents ? "no events" : "possible break";
}

function coverageState(
  registry: Registry,
  support: Registry["support"][number],
  harness: Harness,
  active: Set<string>,
  cell: CellCounts,
): CoverageState {
  if (support.alignment === "not applicable") return notApplicableState(cell);
  if (cell.own > 0)
    return cell.unexplained > 0 ? "stray (unexplained)" : "healthy";
  return silentState(registry, support, harness, active);
}

const ownRows = (
  counts: RouteCounts,
  support: Registry["support"][number],
  harness: Harness,
) =>
  support.route === ""
    ? 0
    : (counts.total.get(support.route)?.get(harness) ?? 0);

/** The chip state is the coverage state seen from one panel. */
const CHIP_STATE: Partial<Record<CoverageState, ContributionState>> = {
  healthy: "rows",
  "stray (unexplained)": "rows",
  idle: "no activity",
  "possible break": "route missing",
  "no events": "no events",
  "not applicable": "not applicable",
  "stray (explained)": "not applicable",
};

/** One enabled harness's chip for a signal, from its coverage state. */
function chip(
  registry: Registry,
  counts: RouteCounts,
  support: Registry["support"][number],
  active: Set<string>,
): Contribution {
  const harness = support.harness;
  const own = ownRows(counts, support, harness);
  if (support.alignment === "not applicable")
    return { state: "not applicable", rows: own, from: null };
  if (own === 0)
    return {
      state: CHIP_STATE[silentState(registry, support, harness, active)]!,
      rows: 0,
      from: null,
    };
  const from = counts.first.get(support.route)?.get(harness) ?? null;
  return { state: "rows", rows: own, from };
}

/**
 * Chip state per enabled harness for every harness-scoped signal of a view that
 * is shown on this machine, reusing the coverage grid's state. `from` is the day
 * a route's first row arrived when that falls inside the window.
 */
export function contributions(
  registry: Registry,
  counts: RouteCounts,
  view: string,
): Contributions {
  const active = harnessesWithRows(registry, counts);
  const enabled = enabledHarnesses(registry);
  const result: Contributions = {};
  for (const signal of registry.signals) {
    if (signal.view !== view || signal.scope !== "harness") continue;
    if (isHidden(registry, signal.signal)) continue;
    const perHarness: Partial<Record<Harness, Contribution>> = {};
    for (const harness of enabled) {
      const support = routeOf(registry, signal.signal, harness);
      if (support)
        perHarness[harness] = chip(registry, counts, support, active);
    }
    result[signal.signal] = perHarness;
  }
  return result;
}

/**
 * Coverage grid: registry alignment versus data per signal × harness, for the
 * signals shown on this machine. A harness is active when any of its routes has
 * rows in the window, so a route without rows is a possible break only while the
 * harness is otherwise active, and only for routes that must have rows; a
 * rare-event route reports no events. Rows on another harness's route of the
 * same signal are stray: explained when a registered stray covers them,
 * unexplained otherwise. A signal that is not applicable to a harness should
 * have no rows at all, so any rows flag it. A harness this machine does not
 * enable is not in use; rows on its own route are flagged, since its producer
 * still exports.
 */
export function coverageGrid(registry: Registry, counts: RouteCounts): Row[] {
  const active = harnessesWithRows(registry, counts);
  const enabled = new Set<string>(enabledHarnesses(registry));
  const rows: Row[] = [];
  for (const signal of registry.signals) {
    if (signal.scope !== "harness" || isHidden(registry, signal.signal))
      continue;
    const signalRoutes = new Set(
      registry.support
        .filter((entry) => entry.signal === signal.signal && entry.route)
        .map((entry) => entry.route),
    );
    for (const harness of HARNESSES) {
      const support = routeOf(registry, signal.signal, harness);
      if (!support) continue;
      const own = ownRows(counts, support, harness);
      const { foreign, unexplained } = foreignCounts(
        registry,
        counts,
        signalRoutes,
        { route: support.route, harness },
      );
      const state: CoverageState = !enabled.has(harness)
        ? own > 0
          ? "not in use, rows present"
          : "not in use"
        : coverageState(registry, support, harness, active, {
            own,
            foreign,
            unexplained,
          });
      rows.push({
        signal: signal.signal,
        title: signal.title,
        view: signal.view,
        harness,
        alignment: support.alignment,
        route: support.route,
        own,
        foreign,
        unexplained,
        state,
      });
    }
  }
  return rows;
}

/** Rows no route or registered stray claims, by harness and name. */
export function unroutedQuery(registry: Registry): string {
  const parts = SOURCES.map((source) => {
    const claimed = registry.routes
      .filter((route) => route.source === source)
      .map((route) => `(harness = '${route.harness}' AND (${route.match}))`);
    return `SELECT '${source}' AS source, harness, toString(${NAME_COLUMN[source]}) AS name, count() AS n
FROM ${SOURCE_RELATION[source]}
WHERE ${WINDOW} AND NOT (${claimed.join(" OR ") || "0"}) AND NOT (${strayCondition(registry, source)})
GROUP BY harness, name`;
  });
  return `SELECT * FROM (${parts.join(" UNION ALL ")}) ORDER BY n DESC LIMIT 40`;
}

const LOADERS = `SELECT view, toString(status) AS status, toString(last_success_time) AS last_success,
    dateDiff('second', last_success_time, now()) AS age_seconds,
    toUInt8(exception != '' OR now() > next_refresh_time + INTERVAL 2 MINUTE) AS stale,
    exception, written_rows
FROM system.view_refreshes WHERE database = 'introspection' ORDER BY view`;

const FRESHNESS = `WITH
    facts AS (
        SELECT 'spans' AS source, harness, max(ts) AS latest, toUInt8(1) AS present FROM introspection.spans
        WHERE ts > now() - INTERVAL 1 DAY GROUP BY harness
        UNION ALL
        SELECT 'logs', harness, max(ts), toUInt8(1) FROM introspection.logs
        WHERE ts > now() - INTERVAL 1 DAY GROUP BY harness
    ),
    raw AS (
        SELECT 'spans' AS source, serviceName AS harness, max(timestamp) AS latest
        FROM signoz_traces.distributed_signoz_index_v3
        WHERE timestamp > now() - INTERVAL 1 DAY AND timestamp < now() - INTERVAL 1 MINUTE
            AND serviceName IN (${SERVICES})
            AND (serviceName NOT LIKE 'codex%' OR ${CODEX_LOADED})
        GROUP BY harness
        UNION ALL
        SELECT 'logs', resource.\`service.name\`::String, fromUnixTimestamp64Nano(toInt64(max(timestamp)), 'UTC')
        FROM signoz_logs.distributed_logs_v2
        WHERE timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL 1 DAY)) * 1000000000
            AND observed_timestamp < toUInt64(toUnixTimestamp(now() - INTERVAL 1 MINUTE)) * 1000000000
            AND resource.\`service.name\`::String IN (${SERVICES})
        GROUP BY 2
    )
SELECT r.source AS source, r.harness AS harness, toString(r.latest) AS source_latest,
    -- An unmatched join yields a default, not a timestamp: report no facts, never a lag.
    toUInt8(ifNull(f.present, 0) = 0) AS facts_missing,
    if(facts_missing = 1, NULL, toString(f.latest)) AS fact_latest,
    if(facts_missing = 1, NULL, greatest(dateDiff('second', f.latest, r.latest), 0)) AS lag_seconds,
    dateDiff('second', r.latest, now()) AS source_age_seconds
FROM raw AS r LEFT JOIN facts AS f ON f.source = r.source AND f.harness = r.harness
ORDER BY harness, source`;

/**
 * Direct recount of the raw SigNoz usage source with the validated producer
 * contracts, against usage_events. The check covers the last 7 days of the
 * selected window, because a raw recount scans SigNoz's full trace table, and
 * ends 10 minutes before now so rows the loaders have not copied yet are not
 * reported as drift.
 */
const PARITY_WINDOW_END =
  "least({end:DateTime64(3, 'UTC')}, now64(3) - INTERVAL 10 MINUTE)";
const PARITY_WINDOW_START = `greatest({start:DateTime64(3, 'UTC')}, ${PARITY_WINDOW_END} - INTERVAL 7 DAY)`;
const PARITY = `WITH
    raw AS (
        SELECT resource.\`service.name\`::String AS harness, count() AS operations,
            sum(if(mapContains(attributes_number, 'input_token_count'), attributes_number['input_token_count'], toFloat64OrZero(attributes_string['input_token_count']))) AS input,
            sum(if(mapContains(attributes_number, 'cached_token_count'), attributes_number['cached_token_count'], toFloat64OrZero(attributes_string['cached_token_count']))) AS cached,
            sum(if(mapContains(attributes_number, 'output_token_count'), attributes_number['output_token_count'], toFloat64OrZero(attributes_string['output_token_count']))) AS output
        FROM signoz_logs.distributed_logs_v2
        WHERE timestamp >= toUInt64(toUnixTimestamp64Nano(${PARITY_WINDOW_START}))
            AND timestamp < toUInt64(toUnixTimestamp64Nano(${PARITY_WINDOW_END}))
            AND resource.\`service.name\`::String IN ('codex-app-server', 'codex_cli_rs', 'codex_exec')
            AND attributes_string['event.name'] = 'codex.sse_event'
            AND attributes_string['conversation.id'] != '' AND attributes_string['model'] != ''
            AND (mapContains(attributes_number, 'input_token_count') OR mapContains(attributes_string, 'input_token_count'))
        GROUP BY harness
        UNION ALL
        SELECT 'claude-code', count(),
            sum(attributes_number['input_tokens'] + attributes_number['cache_creation_tokens'] + attributes_number['cache_read_tokens']),
            sum(attributes_number['cache_read_tokens']), sum(attributes_number['output_tokens'])
        FROM signoz_logs.distributed_logs_v2
        WHERE timestamp >= toUInt64(toUnixTimestamp64Nano(${PARITY_WINDOW_START}))
            AND timestamp < toUInt64(toUnixTimestamp64Nano(${PARITY_WINDOW_END}))
            AND resource.\`service.name\`::String = 'claude-code'
            AND attributes_string['event.name'] = 'api_request'
            AND attributes_string['session.id'] != '' AND attributes_string['model'] != ''
        UNION ALL
        SELECT 'oh-my-pi', count(), sum(numbers['gen_ai.usage.input_tokens']),
            sum(if(mapContains(numbers, 'gen_ai.usage.cache_read.input_tokens'), numbers['gen_ai.usage.cache_read.input_tokens'], 0)),
            sum(numbers['gen_ai.usage.output_tokens'])
        FROM (
            -- SigNoz can hold several identical copies of one span; the facts keep one.
            SELECT spanID, any(attributes_number) AS numbers
            FROM signoz_traces.distributed_signoz_index_v3
            WHERE timestamp >= ${PARITY_WINDOW_START} AND timestamp < ${PARITY_WINDOW_END}
                AND serviceName = 'oh-my-pi'
                AND attributes_string['gen_ai.operation.name'] = 'chat'
                AND attributes_string['gen_ai.conversation.id'] != ''
                AND attributes_string['gen_ai.request.model'] != ''
                AND mapContains(attributes_number, 'gen_ai.usage.input_tokens')
                AND (attributes_number['gen_ai.usage.input_tokens'] > 0 OR attributes_number['gen_ai.usage.output_tokens'] > 0)
            GROUP BY spanID
        )
    ),
    facts AS (
        SELECT harness, count() AS operations, sum(input_tokens) AS input, sum(cached_input) AS cached,
            sum(output_tokens) AS output
        FROM introspection.usage_events_snapshot
        WHERE ts >= ${PARITY_WINDOW_START} AND ts < ${PARITY_WINDOW_END}
        GROUP BY harness
    )
SELECT toString(${PARITY_WINDOW_START}) AS window_start, toString(${PARITY_WINDOW_END}) AS window_end,
    -- A full join also checks harnesses present only in facts; a missing side counts as 0.
    if(r.harness != '', r.harness, f.harness) AS harness,
    ifNull(r.operations, 0) AS raw_operations, ifNull(f.operations, 0) AS fact_operations,
    ifNull(r.input, 0) AS raw_input, ifNull(f.input, 0) AS fact_input,
    ifNull(r.cached, 0) AS raw_cached, ifNull(f.cached, 0) AS fact_cached,
    ifNull(r.output, 0) AS raw_output, ifNull(f.output, 0) AS fact_output,
    toUInt8(raw_operations = fact_operations AND raw_input = fact_input
        AND raw_cached = fact_cached AND raw_output = fact_output) AS ok
FROM raw AS r FULL OUTER JOIN facts AS f ON f.harness = r.harness
WHERE raw_operations > 0 OR fact_operations > 0
ORDER BY harness`;

/**
 * All = Σ harnesses. Each check computes the All value directly (no harness
 * grouping) and from per-harness groups, for counts and for ratio numerators
 * and denominators. Session counts are harness-scoped, so they must also add up.
 */
const RECOMBINATION: Record<
  string,
  { table: string; ts: string; measures: string[] }
> = {
  usage: {
    table: "introspection.usage_events_snapshot",
    ts: "ts",
    measures: [
      "count() AS operations",
      "sum(input_tokens) AS input",
      "sum(cached_input) AS cached",
      "sum(output_tokens) AS output",
      "uniqExact(harness, session_id) AS sessions",
    ],
  },
  tasks: {
    table: "introspection.task_outcomes_snapshot",
    ts: "start_ts",
    measures: [
      "count() AS tasks",
      "sum(clean_completion) AS clean_n",
      "count(clean_completion) AS clean_den",
      "sum(interrupted) AS interrupted_n",
      "count(interrupted) AS interrupted_den",
    ],
  },
  tools: {
    table: "introspection.tool_calls_snapshot",
    ts: "ts",
    measures: [
      "count() AS calls",
      "countIf(outcome = 'failed') AS failed",
      "countIf(outcome IN ('succeeded', 'failed', 'aborted')) AS explicit",
    ],
  },
  provider: {
    table: "introspection.model_calls_snapshot",
    ts: "ts",
    measures: ["count() AS calls", "countIf(outcome = 'failed') AS failed"],
  },
};

const measureNames = (measures: string[]) =>
  measures.map((measure) => measure.split(" AS ")[1]!);

export function recombinationQueries(): Record<string, string> {
  const queries: Record<string, string> = {};
  for (const [check, spec] of Object.entries(RECOMBINATION)) {
    const where = `${spec.ts} >= {start:DateTime64(3, 'UTC')} AND ${spec.ts} < {end:DateTime64(3, 'UTC')}`;
    queries[`${check}:all`] =
      `SELECT ${spec.measures.join(", ")} FROM ${spec.table} WHERE ${where}`;
    queries[`${check}:harness`] =
      `SELECT harness, ${spec.measures.join(", ")} FROM ${spec.table} WHERE ${where} GROUP BY harness`;
  }
  return queries;
}

const sumMeasure = (grouped: Row[], name: string): number =>
  grouped.reduce((total, row) => total + Number(row[name] ?? 0), 0);

/** Compares each direct All aggregate with the sum of its per-harness rows. */
export function recombine(results: Record<string, Row[]>): Row[] {
  const rows: Row[] = [];
  for (const [check, spec] of Object.entries(RECOMBINATION)) {
    const all = results[`${check}:all`] ?? [];
    const grouped = results[`${check}:harness`] ?? [];
    for (const name of measureNames(spec.measures)) {
      const direct = Number(all[0]?.[name] ?? 0);
      const summed = sumMeasure(grouped, name);
      rows.push({
        check,
        measure: name,
        all: direct,
        sum_of_harnesses: summed,
        harnesses: grouped.length,
        ok: Math.abs(direct - summed) < 1e-6,
      });
    }
  }
  return rows.sort((a, b) =>
    `${a.check}${a.measure}`.localeCompare(`${b.check}${b.measure}`),
  );
}

// A home path is `/Users/<name>`; the bare `/Users/` directory names nobody.
const SANITIZATION = `SELECT 'spans' AS source, count() AS rows,
    countIf(hasAny(mapKeys(attrs_string), ${FORBIDDEN_KEYS})) AS forbidden_keys,
    countIf(lengthUTF8(status_message) > 160) AS long_status,
    countIf(match(name, '/Users/[A-Za-z0-9._-]+') OR match(status_message, '/Users/[A-Za-z0-9._-]+')) AS home_paths
FROM introspection.spans
UNION ALL
SELECT 'logs', count(), countIf(hasAny(mapKeys(attrs_string), ${FORBIDDEN_KEYS})),
    countIf(lengthUTF8(attrs_string['x.failure_signature']) > 160),
    countIf(match(attrs_string['x.workdir'], '/Users/[A-Za-z0-9._-]+') OR match(attrs_string['x.targets'], '/Users/[A-Za-z0-9._-]+')
        OR match(attrs_string['x.failure_signature'], '/Users/[A-Za-z0-9._-]+'))
FROM introspection.logs
UNION ALL
SELECT 'hook_events', count(), countIf(hasAny(mapKeys(attrs_string), ${HOOK_FORBIDDEN_KEYS})),
    countIf(lengthUTF8(attrs_string['failure_signature']) > 160),
    countIf(match(attrs_string['workdir'], '/Users/[A-Za-z0-9._-]+') OR match(attrs_string['targets'], '/Users/[A-Za-z0-9._-]+')
        OR match(attrs_string['failure_signature'], '/Users/[A-Za-z0-9._-]+'))
FROM introspection.hook_events`;

/**
 * Per harness: tasks, tasks whose session has a project, and tasks whose session the
 * resolver found in no session store (for example a run that kept no session).
 */
const PROJECT_COVERAGE = `SELECT t.harness AS harness, count() AS tasks,
    countIf(s.status = 'attributed') AS attributed,
    countIf(s.status = '') AS no_session_record
FROM introspection.task_outcomes_snapshot AS t
LEFT JOIN (
    SELECT session_id, any(status) AS status FROM introspection.session_projects FINAL GROUP BY session_id
) AS s ON s.session_id = t.session_id
WHERE t.start_ts >= {start:DateTime64(3, 'UTC')} AND t.start_ts < {end:DateTime64(3, 'UTC')}
GROUP BY harness ORDER BY harness`;

/** Sessions the resolver could not attribute, by session store and reason. */
const PROJECT_REJECTIONS = `SELECT store, status AS reason, count() AS n
FROM introspection.session_projects FINAL
WHERE status != 'attributed'
    AND started_at >= {start:DateTime64(3, 'UTC')} AND started_at < {end:DateTime64(3, 'UTC')}
GROUP BY store, reason ORDER BY n DESC`;

/** When the resolver last stored a session, and how many it holds. */
const PROJECT_SYNC = `SELECT toString(max(loaded_at)) AS last_load,
    dateDiff('second', max(loaded_at), now64(3)) AS age_seconds, count() AS events
FROM introspection.session_projects FINAL`;

const DAILY_ROWS = `SELECT toString(toDate(ts)) AS day, harness, source, count() AS n FROM (
    SELECT ts, harness, 'spans' AS source FROM introspection.spans FINAL WHERE ${inWindow("ts")}
    UNION ALL
    SELECT ts, harness, 'logs' FROM introspection.logs FINAL WHERE ${inWindow("ts")}
    UNION ALL
    SELECT ts, harness, 'hooks' FROM introspection.hook_rows WHERE ${inWindow("ts")}
) GROUP BY day, harness, source ORDER BY day`;

export async function pipelineData(
  query: Query,
  registry: Registry,
  params: Params,
  counts: RouteCounts,
  withinSnapshots = true,
): Promise<Record<string, Row[]>> {
  const queries: Record<string, string> = {
    loaders: LOADERS,
    freshness: FRESHNESS,
    parity: PARITY,
    sanitization: SANITIZATION,
    daily_rows: DAILY_ROWS,
    unrouted: unroutedQuery(registry),
    project_coverage: PROJECT_COVERAGE,
    project_rejections: PROJECT_REJECTIONS,
    project_sync: PROJECT_SYNC,
    ...recombinationQueries(),
  };
  const results = await batch(
    query,
    withinSnapshots ? queries : live(queries),
    params,
  );
  return {
    loaders: results.loaders!,
    freshness: results.freshness!,
    parity: results.parity!,
    recombination: recombine(results),
    sanitization: results.sanitization!,
    daily_rows: results.daily_rows!,
    coverage: coverageGrid(registry, counts),
    unrouted: results.unrouted!,
    project_coverage: results.project_coverage!,
    project_rejections: results.project_rejections!,
    project_sync: results.project_sync!,
    strays: registry.strays.map((entry) => ({ ...entry })),
    exclusions: registry.exclusions.map((entry) => ({ ...entry })),
    hidden: registry.hidden.map((entry) => ({ ...entry })),
  };
}
