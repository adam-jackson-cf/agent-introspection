import type {
  Contributions,
  Harness,
  Registry,
  Row,
  SignalRoute,
} from "../src/contracts";
import { HARNESSES } from "../src/contracts";
import { batch, type Query } from "./clickhouse";
import { inWindow } from "./views";

type Params = Record<string, string>;
type Source = "spans" | "logs";
type RouteCounts = {
  /** route → harness → rows matching the route predicate. */
  total: Map<string, Map<string, number>>;
  /** route → harness → matching rows not explained by a registered stray. */
  unexplained: Map<string, Map<string, number>>;
};

const WINDOW =
  "ts >= {start:DateTime64(3, 'UTC')} AND ts < {end:DateTime64(3, 'UTC')}";
const SOURCES: Source[] = ["spans", "logs"];
/** Span names the loader keeps for Codex; mirrors select_spans.sql. */
const CODEX_SPAN_NAMES =
  "'session_task.turn', 'session_task.run', 'run_sampling_request', 'try_run_sampling_request', 'turn/start', 'turn/interrupt', 'turn/steer', 'handle_responses'";
const SERVICES =
  "'codex-app-server', 'codex_cli_rs', 'codex_exec', 'oh-my-pi', 'claude-code'";
const FORBIDDEN_KEYS =
  "['prompt', 'user_prompt', 'arguments', 'output', 'content', 'user.email', 'user.account_id', 'user.account_uuid', 'user.id', 'organization.id', 'gen_ai.tool.description', 'pi.gen_ai.tool.call.intent']";

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
 * the registry tables that `facts install` loaded from the repo-owned file.
 */
export function routeCountQueries(registry: Registry): Record<string, string> {
  const queries: Record<string, string> = {};
  for (const source of SOURCES) {
    const predicates = routePredicates(registry, source);
    if (predicates.length === 0) continue;
    const stray = strayCondition(registry, source);
    const columns = predicates.flatMap(([, match], index) => [
      `countIf(${match}) AS t${index}`,
      `countIf((${match}) AND NOT (${stray})) AS u${index}`,
    ]);
    queries[source] =
      `SELECT harness, ${columns.join(", ")} FROM introspection.${source} FINAL WHERE ${WINDOW} GROUP BY harness`;
  }
  return queries;
}

/** Interprets the per-source count rows of `routeCountQueries`. */
export function countsFromRows(
  registry: Registry,
  rows: Record<string, Row[]>,
): RouteCounts {
  const total = new Map<string, Map<string, number>>();
  const unexplained = new Map<string, Map<string, number>>();
  for (const source of SOURCES)
    routePredicates(registry, source).forEach(([route], index) => {
      const totals = new Map<string, number>();
      const rest = new Map<string, number>();
      for (const row of rows[source] ?? []) {
        totals.set(String(row.harness), Number(row[`t${index}`]));
        rest.set(String(row.harness), Number(row[`u${index}`]));
      }
      total.set(route, totals);
      unexplained.set(route, rest);
    });
  return { total, unexplained };
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

/** Rows on each harness's own route for every harness-scoped signal of a view. */
export function contributions(
  registry: Registry,
  counts: RouteCounts,
  view: string,
): Contributions {
  const result: Contributions = {};
  for (const signal of registry.signals) {
    if (signal.view !== view || signal.scope !== "harness") continue;
    const perHarness: Partial<Record<Harness, number>> = {};
    for (const harness of HARNESSES) {
      const support = routeOf(registry, signal.signal, harness);
      if (support && support.route !== "")
        perHarness[harness] =
          counts.total.get(support.route)?.get(harness) ?? 0;
    }
    result[signal.signal] = perHarness;
  }
  return result;
}

export type CoverageState =
  | "healthy"
  | "idle"
  | "possible break"
  | "no events"
  | "not emitted"
  | "stray (explained)"
  | "stray (unexplained)";

/**
 * Coverage grid: registry alignment versus data per signal × harness. A harness
 * is active when any of its routes has rows in the window, so an emitted route
 * without rows is a possible break only while the harness is otherwise active,
 * and only for routes that must have rows; a rare-event route reports no events.
 * Rows on another harness's route of the same signal are stray: explained when
 * a registered stray covers them, unexplained otherwise.
 */
export function coverageGrid(registry: Registry, counts: RouteCounts): Row[] {
  const active = new Set<string>();
  for (const route of registry.routes)
    if ((counts.total.get(route.route)?.get(route.harness) ?? 0) > 0)
      active.add(route.harness);
  const rows: Row[] = [];
  for (const signal of registry.signals) {
    if (signal.scope !== "harness") continue;
    const signalRoutes = new Set(
      registry.support
        .filter((entry) => entry.signal === signal.signal && entry.route)
        .map((entry) => entry.route),
    );
    for (const harness of HARNESSES) {
      const support = routeOf(registry, signal.signal, harness);
      if (!support) continue;
      const own =
        support.route === ""
          ? 0
          : (counts.total.get(support.route)?.get(harness) ?? 0);
      let foreign = 0;
      let unexplained = 0;
      for (const route of signalRoutes) {
        if (route === support.route) continue;
        if (
          registry.routes.some(
            (candidate: SignalRoute) =>
              candidate.route === route && candidate.harness === harness,
          )
        )
          continue;
        foreign += counts.total.get(route)?.get(harness) ?? 0;
        unexplained += counts.unexplained.get(route)?.get(harness) ?? 0;
      }
      const state: CoverageState =
        support.alignment === "not emitted"
          ? unexplained > 0
            ? "stray (unexplained)"
            : foreign > 0
              ? "stray (explained)"
              : "not emitted"
          : own > 0
            ? unexplained > 0
              ? "stray (unexplained)"
              : "healthy"
            : !active.has(harness)
              ? "idle"
              : registry.routes.some(
                    (route) =>
                      route.route === support.route &&
                      route.harness === harness &&
                      route.expect === "events",
                  )
                ? "no events"
                : "possible break";
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
    const nameColumn = source === "spans" ? "name" : "event_name";
    return `SELECT '${source}' AS source, harness, ${nameColumn} AS name, count() AS n
FROM introspection.${source} FINAL
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
        SELECT 'spans' AS source, harness, max(ts) AS latest FROM introspection.spans
        WHERE ts > now() - INTERVAL 1 DAY GROUP BY harness
        UNION ALL
        SELECT 'logs', harness, max(ts) FROM introspection.logs
        WHERE ts > now() - INTERVAL 1 DAY GROUP BY harness
    ),
    raw AS (
        SELECT 'spans' AS source, serviceName AS harness, max(timestamp) AS latest
        FROM signoz_traces.distributed_signoz_index_v3
        WHERE timestamp > now() - INTERVAL 1 DAY AND timestamp < now() - INTERVAL 1 MINUTE
            AND serviceName IN (${SERVICES})
            AND (serviceName NOT LIKE 'codex%' OR name IN (${CODEX_SPAN_NAMES}))
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
    toString(f.latest) AS fact_latest,
    greatest(dateDiff('second', f.latest, r.latest), 0) AS lag_seconds,
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
        SELECT 'oh-my-pi', count(), sum(attributes_number['gen_ai.usage.input_tokens']),
            sum(if(mapContains(attributes_number, 'gen_ai.usage.cache_read.input_tokens'), attributes_number['gen_ai.usage.cache_read.input_tokens'], 0)),
            sum(attributes_number['gen_ai.usage.output_tokens'])
        FROM (
            SELECT DISTINCT spanID, attributes_number
            FROM signoz_traces.distributed_signoz_index_v3
            WHERE timestamp >= ${PARITY_WINDOW_START} AND timestamp < ${PARITY_WINDOW_END}
                AND serviceName = 'oh-my-pi'
                AND attributes_string['gen_ai.operation.name'] = 'chat'
                AND attributes_string['gen_ai.conversation.id'] != ''
                AND attributes_string['gen_ai.request.model'] != ''
                AND mapContains(attributes_number, 'gen_ai.usage.input_tokens')
                AND (attributes_number['gen_ai.usage.input_tokens'] > 0 OR attributes_number['gen_ai.usage.output_tokens'] > 0)
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
    r.harness AS harness, r.operations AS raw_operations, f.operations AS fact_operations,
    r.input AS raw_input, f.input AS fact_input, r.cached AS raw_cached, f.cached AS fact_cached,
    r.output AS raw_output, f.output AS fact_output,
    toUInt8(r.operations = f.operations AND r.input = f.input AND r.cached = f.cached AND r.output = f.output) AS ok
FROM raw AS r LEFT JOIN facts AS f ON f.harness = r.harness
WHERE r.operations > 0 OR f.operations > 0
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

/** Compares each direct All aggregate with the sum of its per-harness rows. */
export function recombine(results: Record<string, Row[]>): Row[] {
  const rows: Row[] = [];
  for (const [check, spec] of Object.entries(RECOMBINATION)) {
    const all = results[`${check}:all`] ?? [];
    const grouped = results[`${check}:harness`] ?? [];
    for (const name of measureNames(spec.measures)) {
      const direct = Number(all[0]?.[name] ?? 0);
      const summed = grouped.reduce(
        (total, row) => total + Number(row[name] ?? 0),
        0,
      );
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

const SANITIZATION = `SELECT 'spans' AS source, count() AS rows,
    countIf(hasAny(mapKeys(attrs_string), ${FORBIDDEN_KEYS})) AS forbidden_keys,
    countIf(length(status_message) > 160) AS long_status,
    countIf(position(name, '/Users/') > 0 OR position(status_message, '/Users/') > 0) AS home_paths
FROM introspection.spans
UNION ALL
SELECT 'logs', count(), countIf(hasAny(mapKeys(attrs_string), ${FORBIDDEN_KEYS})), 0,
    countIf(position(attrs_string['x.workdir'], '/Users/') > 0 OR position(attrs_string['x.targets'], '/Users/') > 0
        OR position(attrs_string['x.failure_signature'], '/Users/') > 0)
FROM introspection.logs`;

const DAILY_ROWS = `SELECT toString(toDate(ts)) AS day, harness, source, count() AS n FROM (
    SELECT ts, harness, 'spans' AS source FROM introspection.spans FINAL WHERE ${inWindow("ts")}
    UNION ALL
    SELECT ts, harness, 'logs' FROM introspection.logs FINAL WHERE ${inWindow("ts")}
) GROUP BY day, harness, source ORDER BY day`;

export async function pipelineData(
  query: Query,
  registry: Registry,
  params: Params,
  counts: RouteCounts,
): Promise<Record<string, Row[]>> {
  const results = await batch(
    query,
    {
      loaders: LOADERS,
      freshness: FRESHNESS,
      parity: PARITY,
      sanitization: SANITIZATION,
      daily_rows: DAILY_ROWS,
      unrouted: unroutedQuery(registry),
      ...recombinationQueries(),
    },
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
    strays: registry.strays.map((entry) => ({ ...entry })),
  };
}
