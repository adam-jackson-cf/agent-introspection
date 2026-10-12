import { HARNESSES, type ViewId } from "../src/contracts";
import { EFFORT_ORDER } from "../src/format";

/**
 * Aggregate queries per question-led view. Every query reads the snapshot
 * tables that `facts install` refreshes after each minute load, binds the
 * window and harness as ClickHouse parameters, and returns per-harness rows so
 * the companion can recombine All from numerators and denominators.
 */
export type ViewQueries = Record<string, string>;

const U = "introspection.usage_events_snapshot";
const T = "introspection.task_outcomes_snapshot";
const C = "introspection.tool_calls_snapshot";
const S = "introspection.user_signals_snapshot";
const M = "introspection.model_calls_snapshot";
const L = "introspection.task_labels_snapshot";
/** Activity-hook events with the harness resolved (a view: never FINAL). */
const H = "introspection.hook_rows";
/** Session → Git project, resolved from the harness session stores. */
export const P = "introspection.session_project";

/** Rates over fewer samples than this are shown dimmed beside their n. */
export const MIN_RATE_N = 20;

const START = "{start:DateTime64(3, 'UTC')}";
const END = "{end:DateTime64(3, 'UTC')}";
/** The harnesses this machine uses (`facts install` loads them from the config). */
export const ENABLED_HARNESSES =
  "(SELECT harness FROM introspection.harnesses WHERE enabled = 1)";
/** The selected harness, or All: the union of the enabled harnesses. */
const harnessFilter = (column: string): string =>
  `${column} IN ${ENABLED_HARNESSES} AND ({harness:String} = '' OR ${column} = {harness:String})`;
export const HARNESS_FILTER = harnessFilter("harness");

/** Selected window and harness over one timestamp column. */
export const inWindow = (ts: string): string =>
  `${ts} >= ${START} AND ${ts} < ${END} AND ${HARNESS_FILTER}`;
/** The same window for a joined table's alias. */
const inWindowOf = (alias: string, ts: string): string =>
  `${alias}.${ts} >= ${START} AND ${alias}.${ts} < ${END} AND ${harnessFilter(`${alias}.harness`)}`;

const EXPLICIT = "outcome IN ('succeeded', 'failed', 'aborted')";
/** Waiting and status-polling tools repeat identical calls by design (registry: tools.repeats). */
const NOT_POLLING =
  "tool NOT IN ('wait', 'write_stdin', 'wait_agent', 'wait_threads', 'collaborationwait_agent', 'sleep', 'get_goal', 'get_usage_limits')";
const APPROVALS = "('approved', 'approved_with_amendment', 'accept')";
/** Tool + command head + subcommand; omp and Claude Code take the command shape from the activity hooks. */
const OPERATION = "trimBoth(concat(tool, ' ', command_head, ' ', command_sub))";
const ACTIONABLE =
  "(occurrences >= 3 AND tasks >= 2 AND days >= 2) OR (occurrences >= 5 AND tasks >= 3)";
/** A signature is localized when at least this share of its attributed occurrences share one project. */
export const LOCALIZED_SHARE = 0.8;
/**
 * A project root or failure path under a temporary directory marks an evaluation
 * workspace; mirrors findings.EVALUATION_PATH (a ClickHouse string literal).
 */
export const EVALUATION_PATH = String.raw`(^|\\W)(/private)?/(tmp|var/folders)/`;
/**
 * Headless Codex exec has no next prompt (registry: not applicable), so it stays
 * out of next-prompt denominators instead of counting as never corrected.
 */
const NEXT_PROMPT = "harness != 'codex_exec'";

/**
 * Attributed tool failures with a failure class, outside evaluation workspaces,
 * as findings.py reads them. `where` bounds the window over `c.ts`.
 */
const failures = (
  where: string,
) => `SELECT c.harness AS harness, c.tool_family AS tool_family,
        c.failure_class AS failure_class, c.task_id AS task_id, c.ts AS ts, c.tool AS tool
    FROM ${C} AS c LEFT JOIN ${P} AS p ON p.session_id = c.session_id
    WHERE ${where} AND c.outcome = 'failed' AND c.failure_class != '' AND c.task_id != ''
        AND NOT match(p.project_root, '${EVALUATION_PATH}')
        AND NOT match(c.failure_signature, '${EVALUATION_PATH}')`;

/**
 * Failure clusters (tool family × failure class), one finished row per cluster
 * across the selected harnesses: occurrences, tasks (harness-scoped, so distinct
 * (harness, task) pairs), the cluster's own distinct days, the sorted union of
 * tools, the latest occurrence, and `breakdown`, one "harness:occurrences" entry
 * per harness. The thresholds in `having` apply to the same cluster totals.
 */
const clusters = (where: string, having: string) => `WITH
    f AS (${failures(where)}),
    h AS (
        SELECT tool_family, failure_class, arraySort(groupArray(concat(harness, ':', toString(n)))) AS breakdown
        FROM (SELECT harness, tool_family, failure_class, count() AS n FROM f GROUP BY harness, tool_family, failure_class)
        GROUP BY tool_family, failure_class
    )
SELECT tool_family, failure_class, concat(tool_family, ' / ', failure_class) AS key,
    count() AS occurrences, uniqExact(harness, task_id) AS tasks,
    uniqExact(toDate(ts, 'Europe/London')) AS days,
    arraySort(groupUniqArray(tool)) AS tools, any(h.breakdown) AS breakdown,
    toString(max(ts)) AS last_seen
FROM f INNER JOIN h USING (tool_family, failure_class)
GROUP BY tool_family, failure_class HAVING ${having}
ORDER BY tasks DESC, occurrences DESC, tool_family, failure_class LIMIT 30`;

const TASK_OUTCOME_COLUMNS = `
    count() AS tasks,
    sum(clean_completion) AS clean_n, count(clean_completion) AS clean_den,
    sum(interrupted) AS interrupted_n, count(interrupted) AS interrupted_den,
    sum(steered) AS steered_n, count(steered) AS steered_den,
    sum(errored) AS errored_n, count(errored) AS errored_den,
    sum(quick_follow_up) AS follow_up_n, count(quick_follow_up) AS follow_up_den`;

/** Phase 2: `<component>_rate` = 100·n/den (NULL when no task observes it), from the aggregated n and den. */
export const FRICTION_RATES = [
  "clean",
  "interrupted",
  "steered",
  "errored",
  "follow_up",
]
  .map(
    (key) =>
      `if(${key}_den > 0, 100 * ${key}_n / ${key}_den, NULL) AS ${key}_rate`,
  )
  .join(",\n    ");

/** Effort ordinal as format.ts `EFFORT_ORDER` (low … unset); any other value sorts last. */
const EFFORT_RANK = `if(indexOf(['low', 'medium', 'high', 'xhigh', 'mixed', 'unset'], effort) = 0, 7, indexOf(['low', 'medium', 'high', 'xhigh', 'mixed', 'unset'], effort))`;

/**
 * Reasoning spend rows grouped by `by` (from `harness`, `effort`, `day`): usage
 * records for producers that report reasoning per call, and Claude Code tasks,
 * whose reasoning the activity hooks report per turn (registry route
 * hook.turn_stop), each task counting as one call.
 */
const REASONING_USAGE = `SELECT harness, effort, toString(toDate(ts)) AS day, count() AS calls,
        sum(coalesce(reasoning_tokens, 0)) AS reasoning, sum(output_tokens) AS output
    FROM ${U} WHERE ${inWindow("ts")} AND reasoning_tokens IS NOT NULL AND harness != 'claude-code'
    GROUP BY harness, effort, day
    UNION ALL
    SELECT harness, effort, toString(toDate(start_ts)) AS day, count() AS calls,
        sum(coalesce(reasoning_tokens, 0)) AS reasoning, sum(coalesce(output_tokens, 0)) AS output
    FROM ${T} WHERE ${inWindow("start_ts")} AND harness = 'claude-code' AND reasoning_tokens IS NOT NULL
    GROUP BY harness, effort, day`;

/** Corrected-by-next-prompt numerator and denominator over task_labels rows. */
const CORRECTED_N = `countIf(${NEXT_PROMPT} AND corrected_next = 1)`;
const CORRECTED_DEN = `countIf(${NEXT_PROMPT} AND corrected_next IS NOT NULL)`;

/** A percentage as SQL: 100 × num ÷ den, NULL when there is no denominator (A7). */
const rateOf = (numerator: string, denominator: string): string =>
  `if(${denominator} > 0, 100 * ${numerator} / ${denominator}, NULL)`;

/** Jev's task types in classifier order; unknown types follow. */
const TASK_TYPE_ORDER = [
  "bugfix",
  "feature",
  "refactor",
  "investigate",
  "review",
  "ops",
  "docs",
  "continue",
];
/** A repeated correction: a project × correction kind seen in at least this many tasks … */
export const REPEAT_MIN_TASKS = 2;
/** … and in at least this many sessions. */
export const REPEAT_MIN_SESSIONS = 2;

const sqlArray = (items: readonly string[]): string =>
  `[${items.map((item) => `'${item}'`).join(", ")}]`;
/** 1-based position in a fixed order, unknown values after the known ones. */
const orderRank = (order: readonly string[], column: string): string =>
  `if(indexOf(${sqlArray(order)}, ${column}) = 0, ${order.length + 1}, indexOf(${sqlArray(order)}, ${column}))`;

/**
 * Project × correction kind pairs over the selected harnesses, one row each:
 * tasks and sessions are harness-scoped, so sessions count (harness, session).
 */
const REPEATED_PAIRS = `SELECT p.project AS project, l.correction_kind_next AS correction_kind,
    arraySort(h -> (indexOf(${sqlArray(HARNESSES)}, h), h), groupUniqArray(l.harness)) AS harnesses,
    count() AS tasks, uniqExact(l.harness, l.session_id) AS sessions,
    arraySort(t -> (${orderRank(TASK_TYPE_ORDER, "t")}, t), groupUniqArrayIf(l.task_type, l.task_type != '')) AS task_types,
    toString(max(l.start_ts)) AS last_seen
FROM ${L} AS l INNER JOIN ${P} AS p ON p.session_id = l.session_id
WHERE ${inWindowOf("l", "start_ts")} AND l.harness != 'codex_exec' AND l.corrected_next = 1
    AND l.correction_kind_next NOT IN ('', 'none')
    AND p.project != '' AND NOT match(p.project_root, '${EVALUATION_PATH}')
GROUP BY project, correction_kind`;

/** Seven Europe/London calendar days ending on the window end, over an alias's `ts`. */
const LONDON_WEEK_OF = (alias: string) =>
  `toDate(${alias}.ts, 'Europe/London') > toDate(${END}, 'Europe/London') - 7 AND ${alias}.ts < ${END} AND ${harnessFilter(`${alias}.harness`)}`;

export const VIEW_QUERIES: Record<Exclude<ViewId, "pipeline">, ViewQueries> = {
  cache: {
    kpi: `SELECT harness, sum(input_tokens) AS input, sum(cached_input) AS cached,
    sum(coalesce(cache_creation_input, 0)) AS creation, sum(output_tokens) AS output,
    count() AS operations, uniqExact(harness, session_id) AS sessions,
    input - cached AS uncached,
    if(input > 0, 100 * cached / input, NULL) AS cache_rate,
    if(input > 0, 100 * (input - cached) / input, NULL) AS uncached_rate
FROM ${U} WHERE ${inWindow("ts")} GROUP BY harness WITH ROLLUP ORDER BY harness`,
    daily: `SELECT toString(toDate(ts)) AS day, harness, sum(input_tokens) AS input,
    sum(cached_input) AS cached, sum(output_tokens) AS output, input - cached AS uncached,
    if(input > 0, 100 * cached / input, NULL) AS cache_rate
FROM ${U} WHERE ${inWindow("ts")} GROUP BY day, harness WITH ROLLUP HAVING day != '' ORDER BY day, harness`,
    models: `SELECT harness, provider, model, count() AS operations, sum(input_tokens) AS input,
    sum(cached_input) AS cached, sum(coalesce(cache_creation_input, 0)) AS creation, sum(output_tokens) AS output,
    input - cached AS uncached, if(input > 0, 100 * cached / input, NULL) AS cache_rate
FROM ${U} WHERE ${inWindow("ts")} GROUP BY harness, provider, model ORDER BY input DESC LIMIT 40`,
    sessions: `SELECT harness, session_id, arrayStringConcat(arraySort(groupUniqArray(model)), ', ') AS models,
    count() AS operations, sum(input_tokens) AS input, sum(cached_input) AS cached, sum(output_tokens) AS output,
    input - cached AS uncached, if(input > 0, 100 * cached / input, NULL) AS cache_rate,
    toString(min(ts)) AS first_seen, toString(max(ts)) AS last_seen
FROM ${U} WHERE ${inWindow("ts")} GROUP BY harness, session_id
ORDER BY uncached DESC LIMIT 20`,
  },
  effort: {
    usage: `SELECT harness, effort, calls, reasoning, output,
    if(output > 0, 100 * reasoning / output, NULL) AS reasoning_share_rate,
    reasoning / greatest(1, calls) AS per_call,
    if(sum(reasoning) OVER () > 0, 100 * reasoning / sum(reasoning) OVER (), NULL) AS of_all_rate
FROM (
    SELECT harness, effort, sum(calls) AS calls, sum(reasoning) AS reasoning, sum(output) AS output FROM (
    ${REASONING_USAGE}
) GROUP BY harness, effort
) ORDER BY harness, ${EFFORT_RANK}, effort`,
    spend: `SELECT sum(calls) AS calls, sum(reasoning) AS reasoning, sum(output) AS output,
    if(output > 0, 100 * reasoning / output, NULL) AS reasoning_share_rate
FROM (
    ${REASONING_USAGE}
)`,
    outcomes: `SELECT harness, effort, ${TASK_OUTCOME_COLUMNS},
    ${FRICTION_RATES},
    countIf(effort IN ('high', 'xhigh')) AS heavy,
    quantileExact(0.5)(duration_seconds) AS p50_duration, quantileExact(0.9)(duration_seconds) AS p90_duration,
    sum(model_steps) AS steps, sum(t.delegations) AS delegations, count(t.delegations) AS delegations_den,
    sum(coalesce(reasoning_tokens, 0)) AS reasoning, count(reasoning_tokens) AS reasoning_den,
    sum(coalesce(output_tokens, 0)) AS output,
    sumIf(coalesce(output_tokens, 0), reasoning_tokens IS NOT NULL) AS reasoning_output,
    steps / greatest(1, tasks) AS steps_per_task,
    if(delegations_den > 0, delegations / delegations_den, NULL) AS delegations_per_task,
    if(reasoning_den > 0, reasoning / reasoning_den, NULL) AS reasoning_per_task,
    if(reasoning_den > 0 AND reasoning_output > 0, 100 * reasoning / reasoning_output, NULL) AS reasoning_share_rate
FROM ${T} AS t WHERE ${inWindow("start_ts")} GROUP BY harness, effort ORDER BY harness, ${EFFORT_RANK}, effort`,
    summary: `SELECT count() AS tasks, countIf(effort IN ('high', 'xhigh')) AS heavy,
    sum(clean_completion) AS clean_n, count(clean_completion) AS clean_den,
    if(clean_den > 0, 100 * clean_n / clean_den, NULL) AS clean_rate,
    if(tasks > 0, 100 * heavy / tasks, NULL) AS heavy_rate
FROM ${T} WHERE ${inWindow("start_ts")}`,
    daily_tasks: `SELECT toString(toDate(start_ts)) AS day, effort, count() AS tasks,
    sum(clean_completion) AS clean_n, count(clean_completion) AS clean_den,
    if(clean_den > 0, 100 * clean_n / clean_den, NULL) AS clean_rate,
    quantileExact(0.5)(duration_seconds) AS p50_duration, avg(model_steps) AS avg_steps
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY day, effort ORDER BY day, effort`,
    daily_reasoning: `SELECT day, effort, sum(reasoning) AS reasoning FROM (
    ${REASONING_USAGE}
) GROUP BY day, effort ORDER BY day, effort`,
    model_effort: `SELECT harness, model, effort, count() AS tasks,
    sum(clean_completion) AS clean_n, count(clean_completion) AS clean_den,
    if(clean_den > 0, 100 * clean_n / clean_den, NULL) AS clean_rate,
    quantileExact(0.5)(duration_seconds) AS p50_duration, avg(model_steps) AS avg_steps,
    sum(coalesce(reasoning_tokens, 0)) AS reasoning, count(reasoning_tokens) AS reasoning_den,
    sumIf(model_steps, reasoning_tokens IS NOT NULL) AS reasoning_steps,
    if(reasoning_den > 0, reasoning / greatest(1, reasoning_steps), NULL) AS reasoning_per_step
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY harness, model, effort HAVING tasks >= 3
ORDER BY harness, model, ${EFFORT_RANK}, effort`,
    heaviest: `SELECT harness, session_id, task_id, model, effort, toString(start_ts) AS started,
    duration_seconds, model_steps, reasoning_tokens, output_tokens, clean_completion
FROM ${T} WHERE ${inWindow("start_ts")} AND reasoning_tokens IS NOT NULL
ORDER BY reasoning_tokens DESC LIMIT 25`,
  },
  tools: {
    kpi: `SELECT harness, count() AS calls, countIf(${EXPLICIT}) AS explicit,
    countIf(outcome = 'failed') AS failed, countIf(outcome = 'aborted') AS aborted,
    countIf(outcome = 'unknown') AS unknown, countIf(task_id = '') AS unattributed,
    uniqExactIf(task_id, task_id != '') AS tasks,
    uniqExactIf(task_id, task_id != '' AND outcome = 'failed') AS failed_tasks,
    if(explicit > 0, 100 * failed / explicit, NULL) AS failure_rate,
    if(tasks > 0, 100 * failed_tasks / tasks, NULL) AS tasks_affected_rate
FROM ${C} WHERE ${inWindow("ts")} GROUP BY ROLLUP(harness) ORDER BY harness`,
    daily: `SELECT toString(toDate(ts)) AS day, harness, count() AS calls,
    countIf(${EXPLICIT}) AS explicit, countIf(outcome = 'failed') AS failed,
    if(explicit > 0, 100 * failed / explicit, NULL) AS failure_rate
FROM ${C} WHERE ${inWindow("ts")} GROUP BY day, harness ORDER BY day, harness`,
    by_tool: `SELECT harness, tool, count() AS calls, countIf(${EXPLICIT}) AS explicit,
    countIf(outcome = 'failed') AS failed,
    if(explicit > 0, 100 * failed / explicit, NULL) AS failure_rate,
    uniqExactIf(task_id, task_id != '' AND outcome = 'failed') AS failed_tasks, count() OVER () AS total_rows
FROM ${C} WHERE ${inWindow("ts")} GROUP BY harness, tool ORDER BY failed DESC, calls DESC LIMIT 30`,
    signatures: `SELECT harness, tool, argMax(failure_signature, (ts, session_id)) AS signature, count() AS occurrences,
    uniqExactIf(task_id, task_id != '') AS tasks, uniqExact(session_id) AS sessions,
    toString(max(ts)) AS last_seen, argMax(session_id, (ts, session_id)) AS example_session,
    if(failure_signature_hash != '', failure_signature_hash, failure_signature) AS signature_key,
    count() OVER () AS total_rows
FROM ${C} WHERE ${inWindow("ts")} AND outcome = 'failed' AND failure_signature != ''
GROUP BY harness, tool, signature_key ORDER BY occurrences DESC LIMIT 30`,
    repeat_summary: `SELECT harness, uniqExactIf(task_id, attempts >= 2) AS repeat_tasks, uniqExact(task_id) AS tasks,
    sumIf(attempts, attempts >= 2) AS repeated_attempts,
    if(tasks > 0, 100 * repeat_tasks / tasks, NULL) AS repeat_rate
FROM (
    SELECT harness, task_id, tool, arguments_hash, count() AS attempts
    FROM ${C} WHERE ${inWindow("ts")} AND task_id != '' AND arguments_hash != '' AND ${NOT_POLLING}
    GROUP BY harness, task_id, tool, arguments_hash
) GROUP BY ROLLUP(harness) ORDER BY harness`,
    repeats: `SELECT harness, session_id, task_id, tool, any(command_head) AS command, count() AS attempts,
    countIf(outcome = 'failed') AS failed, count() OVER () AS total_rows
FROM ${C} WHERE ${inWindow("ts")} AND task_id != '' AND arguments_hash != '' AND ${NOT_POLLING}
GROUP BY harness, session_id, task_id, tool, arguments_hash HAVING attempts >= 2
ORDER BY attempts DESC LIMIT 25`,
    loops: `WITH
    ordered AS (
        SELECT harness, session_id, task_id, ts, tool, command_head, arguments_hash, outcome,
            concat(tool, char(0), arguments_hash) AS call_key,
            lagInFrame(call_key, 1, '') OVER (
                PARTITION BY harness, task_id ORDER BY ts ROWS BETWEEN 1 PRECEDING AND CURRENT ROW
            ) AS previous
        FROM ${C} WHERE ${inWindow("ts")} AND task_id != '' AND arguments_hash != '' AND ${NOT_POLLING}
    ),
    runs AS (
        SELECT *, sum(toUInt32(call_key != previous)) OVER (
            PARTITION BY harness, task_id ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS run
        FROM ordered
    )
SELECT harness, session_id, task_id, any(tool) AS tool, any(command_head) AS command, count() AS length,
    countIf(outcome = 'failed') AS failed, toString(min(ts)) AS started, count() OVER () AS total_rows
FROM runs GROUP BY harness, session_id, task_id, run HAVING length >= 3
ORDER BY length DESC LIMIT 25`,
  },
  friction: {
    kpi: `SELECT harness, ${TASK_OUTCOME_COLUMNS},
    ${FRICTION_RATES},
    if(harness = '', '', arrayStringConcat(any(friction_observed), ', ')) AS observed
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY ROLLUP(harness) ORDER BY harness`,
    daily: `SELECT toString(toDate(start_ts)) AS day, harness, ${TASK_OUTCOME_COLUMNS},
    ${FRICTION_RATES}
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY day, harness ORDER BY day, harness`,
    daily_signals: `SELECT toString(toDate(start_ts)) AS day, sum(interrupted) AS interrupt, sum(steered) AS steer
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY day ORDER BY day`,
    recovery_summary: `WITH ops AS (
    SELECT harness, task_id, ${OPERATION} AS operation,
        minIf(ts, outcome = 'failed') AS first_failure,
        groupArrayIf(ts, outcome = 'succeeded') AS successes
    FROM ${C} WHERE (harness, task_id) IN (SELECT harness, task_id FROM ${T} WHERE ${inWindow("start_ts")})
        AND task_id != '' AND outcome IN ('failed', 'succeeded')
    GROUP BY harness, task_id, operation HAVING countIf(outcome = 'failed') > 0
)
SELECT harness, count() AS failed_ops,
    countIf(arrayExists(t -> t > first_failure, successes)) AS recovered,
    if(failed_ops > 0, 100 * recovered / failed_ops, NULL) AS recovered_rate,
    quantileExactIf(0.5)(
        dateDiff('millisecond', first_failure, arrayMin(arrayFilter(t -> t > first_failure, successes))) / 1000,
        arrayExists(t -> t > first_failure, successes)
    ) AS p50_recovery_seconds
FROM ops GROUP BY ROLLUP(harness) ORDER BY harness`,
    recovery_ops: `WITH ops AS (
    SELECT harness, task_id, ${OPERATION} AS operation,
        minIf(ts, outcome = 'failed') AS first_failure,
        groupArrayIf(ts, outcome = 'succeeded') AS successes
    FROM ${C} WHERE (harness, task_id) IN (SELECT harness, task_id FROM ${T} WHERE ${inWindow("start_ts")})
        AND task_id != '' AND outcome IN ('failed', 'succeeded')
    GROUP BY harness, task_id, operation HAVING countIf(outcome = 'failed') > 0
)
SELECT harness, operation, count() AS failed_ops, count() OVER () AS total_rows,
    countIf(arrayExists(t -> t > first_failure, successes)) AS recovered
FROM ops GROUP BY harness, operation ORDER BY failed_ops DESC LIMIT 25`,
    tasks: `SELECT harness, session_id, task_id, model, toString(start_ts) AS started, duration_seconds,
    interrupted, steered, errored, quick_follow_up
FROM ${T} WHERE ${inWindow("start_ts")} AND (interrupted = 1 OR steered = 1 OR errored = 1)
ORDER BY start_ts DESC LIMIT 25`,
  },
  guardrails: {
    decisions: `SELECT harness, detail AS decision, source, count() AS n,
    toUInt8(detail NOT IN ${APPROVALS}) AS rejected
FROM ${S} WHERE ${inWindow("ts")} AND signal = 'tool_decision'
GROUP BY harness, decision, source ORDER BY n DESC`,
    decision_kpi: `SELECT '' AS harness, decided, rejected, by_user,
    if(decided > 0, 100 * rejected / decided, NULL) AS rejection_rate
FROM (
    SELECT count() AS decided, countIf(detail NOT IN ${APPROVALS}) AS rejected,
        countIf(startsWith(lowerUTF8(source), 'user')) AS by_user
    FROM ${S} WHERE ${inWindow("ts")} AND signal = 'tool_decision'
)`,
    decision_sources: `SELECT harness, if(source = '', 'unknown', source) AS source_label, count() AS n
FROM ${S} WHERE ${inWindow("ts")} AND signal = 'tool_decision'
GROUP BY harness, source_label ORDER BY n DESC`,
    decisions_daily: `SELECT toString(toDate(ts)) AS day, harness, count() AS decisions,
    countIf(detail NOT IN ${APPROVALS}) AS rejected,
    if(decisions > 0, 100 * rejected / decisions, NULL) AS rejection_rate
FROM ${S} WHERE ${inWindow("ts")} AND signal = 'tool_decision' GROUP BY day, harness ORDER BY day`,
    sandbox: `SELECT harness, detail AS outcome, tool, count() AS n, count() OVER () AS total_rows
FROM ${S} WHERE ${inWindow("ts")} AND signal = 'sandbox_outcome'
GROUP BY harness, outcome, tool ORDER BY n DESC LIMIT 30`,
    sandbox_kpi: `SELECT '' AS harness, outcomes, denied FROM (
    SELECT count() AS outcomes, countIf(detail = 'denied') AS denied
    FROM ${S} WHERE ${inWindow("ts")} AND signal = 'sandbox_outcome'
)`,
    sandbox_daily: `SELECT toString(toDate(ts)) AS day, harness, count() AS outcomes,
    countIf(detail = 'denied') AS denied
FROM ${S} WHERE ${inWindow("ts")} AND signal = 'sandbox_outcome' GROUP BY day, harness ORDER BY day`,
    bypass: `SELECT '' AS harness, sum(commands) AS commands, sum(bypass) AS bypass, sum(tasks) AS tasks FROM (
    SELECT harness, countIf(command_head != '') AS commands, countIf(gate_bypass = 1) AS bypass,
        uniqExactIf(task_id, gate_bypass = 1 AND task_id != '') AS tasks
    FROM ${C} WHERE ${inWindow("ts")} GROUP BY harness
)`,
    bypass_calls: `SELECT substring(toString(ts), 1, 16) AS at, harness, session_id, task_id, command_head, command_sub,
    trimBoth(concat(command_head, ' ', command_sub)) AS command, outcome
FROM ${C} WHERE ${inWindow("ts")} AND gate_bypass = 1 ORDER BY ts DESC LIMIT 25`,
    churn_summary: `SELECT '' AS harness, sum(tasks) AS tasks, sum(pairs) AS pairs FROM (
    SELECT harness, uniqExact(task_id) AS tasks, count() AS pairs FROM (
        SELECT harness, task_id, target,
            uniqExact(concat(tool, ' ', command_head, ' ', command_sub)) AS commands
        FROM ${C} ARRAY JOIN targets AS target
        WHERE ${inWindow("ts")} AND task_id != ''
        GROUP BY harness, task_id, target HAVING commands >= 3
    ) GROUP BY harness
)`,
    churn: `SELECT harness, session_id, task_id, target,
    uniqExact(concat(tool, ' ', command_head, ' ', command_sub)) AS commands, count() AS calls,
    countIf(outcome = 'failed') AS failed
FROM ${C} ARRAY JOIN targets AS target
WHERE ${inWindow("ts")} AND task_id != ''
GROUP BY harness, session_id, task_id, target HAVING commands >= 3
ORDER BY commands DESC, calls DESC LIMIT 25`,
  },
  provider: {
    kpi: `SELECT harness, count() AS calls, countIf(outcome != 'cancelled') AS rated_calls, countIf(outcome = 'failed') AS failed,
    if(rated_calls > 0, 100 * failed / rated_calls, NULL) AS error_rate,
    countIf(outcome = 'cancelled') AS cancelled, countIf(outcome = 'unknown') AS unknown,
    countIf(stream_disconnect = 1) AS disconnects, count(stream_disconnect) AS disconnect_den,
    countIf(attempt > 1) AS retried, count(attempt) AS attempt_den
FROM ${M} WHERE ${inWindow("ts")} GROUP BY ROLLUP(harness) ORDER BY harness`,
    conformance: `SELECT harness, countIf(response_model != '') AS with_response_model,
    countIf(response_model != '' AND response_model != model) AS mismatched,
    if(with_response_model > 0, 100 * mismatched / with_response_model, NULL) AS mismatch_rate
FROM ${M} WHERE ${inWindow("ts")} GROUP BY harness HAVING with_response_model > 0 ORDER BY harness`,
    latency: `SELECT l.harness AS harness, l.model AS model, l.calls AS calls, l.total_rows AS total_rows,
    if(startsWith(l.harness, 'codex'), if(s.matched = 1, s.p50, NULL), l.p50) AS p50,
    if(startsWith(l.harness, 'codex'), if(s.matched = 1, s.p95, NULL), l.p95) AS p95,
    l.ttft_p50 AS ttft_p50, l.ttft_p95 AS ttft_p95, l.tps_p50 AS tps_p50
FROM (
    SELECT harness, model, count() AS calls, count() OVER () AS total_rows,
        quantileExact(0.5)(duration_seconds) AS p50, quantileExact(0.95)(duration_seconds) AS p95,
        quantileExact(0.5)(ttft_seconds) AS ttft_p50, quantileExact(0.95)(ttft_seconds) AS ttft_p95,
        quantileExact(0.5)(if(duration_seconds > ttft_seconds AND output_tokens > 0,
            output_tokens / (duration_seconds - ttft_seconds), NULL)) AS tps_p50
    FROM ${M} WHERE ${inWindow("ts")} GROUP BY harness, model ORDER BY calls DESC LIMIT 30
) AS l LEFT JOIN (
    SELECT harness, attrs_string['model'] AS model, 1 AS matched,
        quantileExactIf(0.5)(duration_ns / 1e9, name = 'run_sampling_request') AS p50,
        quantileExactIf(0.95)(duration_ns / 1e9, name = 'run_sampling_request') AS p95
    FROM introspection.spans FINAL
    WHERE ${inWindow("ts")} AND harness LIKE 'codex%' AND name IN ('run_sampling_request', 'try_run_sampling_request')
    GROUP BY harness, model
) AS s ON l.harness = s.harness AND l.model = s.model
ORDER BY calls DESC`,
    retries: `SELECT harness, sum(extra) AS extra, sum(steps) AS steps, sum(attempts) AS attempts,
    sum(failed_attempts) AS failed_attempts, sum(exhausted) AS exhausted
FROM (
    SELECT harness, toUInt64(if(attempts > steps, attempts - steps, 0)) AS extra, steps, attempts,
        0 AS failed_attempts, 0 AS exhausted
    FROM (
        SELECT harness, countIf(name = 'run_sampling_request') AS steps,
            countIf(name = 'try_run_sampling_request') AS attempts
        FROM introspection.spans FINAL
        WHERE ${inWindow("ts")} AND harness LIKE 'codex%' AND name IN ('run_sampling_request', 'try_run_sampling_request')
        GROUP BY harness
    )
    UNION ALL
    SELECT 'oh-my-pi', (
        SELECT count() FROM ${H}
        WHERE ${inWindow("ts")} AND harness = 'oh-my-pi' AND event_type = 'retry' AND attrs_string['phase'] = 'start'
    ), 0, 0, 0, 0
    WHERE (SELECT count() FROM ${M} WHERE ${inWindow("ts")} AND harness = 'oh-my-pi') > 0
    UNION ALL
    SELECT 'claude-code', (
        SELECT countIf(attempt > 1) FROM ${M} WHERE ${inWindow("ts")} AND harness = 'claude-code'
    ), 0, 0, (
        SELECT countIf(event_name = 'api_error') FROM introspection.logs FINAL
        WHERE ${inWindow("ts")} AND harness = 'claude-code' AND event_name IN ('api_error', 'api_retries_exhausted')
    ), (
        SELECT countIf(event_name = 'api_retries_exhausted') FROM introspection.logs FINAL
        WHERE ${inWindow("ts")} AND harness = 'claude-code' AND event_name IN ('api_error', 'api_retries_exhausted')
    )
    WHERE (SELECT count() FROM ${M} WHERE ${inWindow("ts")} AND harness = 'claude-code') > 0
) GROUP BY ROLLUP(harness)
ORDER BY multiIf(harness LIKE 'codex%', 0, harness = 'oh-my-pi', 1, harness = 'claude-code', 2, 3), harness`,
    daily: `SELECT toString(toDate(ts)) AS day, harness, countIf(outcome != 'cancelled') AS rated_calls,
    countIf(outcome = 'failed') AS failed, if(rated_calls > 0, 100 * failed / rated_calls, NULL) AS error_rate,
    quantileExact(0.5)(ttft_seconds) AS ttft_p50
FROM ${M} WHERE ${inWindow("ts")} GROUP BY day, harness ORDER BY day, harness`,
    errors: `SELECT harness, model, error_class, count() AS n, toString(max(ts)) AS last_seen, count() OVER () AS total_rows
FROM ${M} WHERE ${inWindow("ts")} AND outcome = 'failed'
GROUP BY harness, model, error_class ORDER BY n DESC LIMIT 25`,
  },
  recurrence: {
    targets: `SELECT target, arrayStringConcat(arraySort(groupUniqArray(harness)), ', ') AS harnesses,
    uniqExact(harness, task_id) AS tasks, uniqExact(toDate(ts, 'Europe/London')) AS days,
    count() AS calls, countIf(outcome = 'failed') AS failed, count() OVER () AS total_files
FROM (
    SELECT c.harness AS harness, c.task_id AS task_id, c.ts AS ts, c.outcome AS outcome, target
    FROM ${C} AS c LEFT JOIN ${P} AS p ON p.session_id = c.session_id
    ARRAY JOIN c.targets AS target
    WHERE ${inWindowOf("c", "ts")} AND c.task_id != ''
        AND NOT match(p.project_root, '${EVALUATION_PATH}')
        AND NOT match(c.failure_signature, '${EVALUATION_PATH}')
)
GROUP BY target HAVING tasks >= 2 ORDER BY tasks DESC, calls DESC LIMIT 30`,
    target_summary: `SELECT uniqExact(target) AS targets, uniqExactIf(target, tasks >= 2) AS recurring
FROM (
    SELECT target, uniqExact(harness, task_id) AS tasks
    FROM (
        SELECT c.harness AS harness, c.task_id AS task_id, target
        FROM ${C} AS c LEFT JOIN ${P} AS p ON p.session_id = c.session_id
        ARRAY JOIN c.targets AS target
        WHERE ${inWindowOf("c", "ts")} AND c.task_id != ''
            AND NOT match(p.project_root, '${EVALUATION_PATH}')
            AND NOT match(c.failure_signature, '${EVALUATION_PATH}')
    ) GROUP BY target
)`,
    clusters: clusters(inWindowOf("c", "ts"), "tasks >= 2"),
    cluster_summary: `SELECT count() AS clusters, countIf(tasks >= 2) AS recurring FROM (
    SELECT tool_family, failure_class, uniqExact(harness, task_id) AS tasks
    FROM (${failures(inWindowOf("c", "ts"))})
    GROUP BY tool_family, failure_class
)`,
    actionable: clusters(LONDON_WEEK_OF("c"), ACTIONABLE),
    actionable_summary: `SELECT count() AS actionable FROM (
    SELECT tool_family, failure_class, count() AS occurrences,
        uniqExact(harness, task_id) AS tasks, uniqExact(toDate(ts, 'Europe/London')) AS days
    FROM (${failures(LONDON_WEEK_OF("c"))})
    GROUP BY tool_family, failure_class HAVING ${ACTIONABLE}
)`,
    concentration: `SELECT *, top_count >= ${LOCALIZED_SHARE} * attributed AS localized,
    if(attributed > 0, 100 * top_count / attributed, NULL) AS top_share,
    count() OVER () AS shown, sum(localized) OVER () AS localized_signatures,
    if(shown > 0, 100 * localized_signatures / shown, NULL) AS localized_rate
FROM (
    SELECT harness, any(signature) AS signature, sum(n) AS occurrences, sumIf(n, project != '') AS attributed,
        maxIf(n, project != '') AS top_count, argMaxIf(project, n, project != '') AS top_project,
        uniqExactIf(project, project != '') AS projects, count() OVER () AS total_signatures
    FROM (
        SELECT c.harness AS harness, if(c.failure_signature_hash != '', c.failure_signature_hash, c.failure_signature) AS signature_key,
            any(c.failure_signature) AS signature, p.project AS project, count() AS n
        FROM ${C} AS c LEFT JOIN ${P} AS p ON p.session_id = c.session_id
        WHERE ${inWindowOf("c", "ts")} AND c.outcome = 'failed' AND c.failure_signature != ''
            AND NOT match(p.project_root, '${EVALUATION_PATH}')
            AND NOT match(c.failure_signature, '${EVALUATION_PATH}')
        GROUP BY harness, signature_key, project
    ) GROUP BY harness, signature_key HAVING attributed > 0 AND occurrences >= 3
    ORDER BY occurrences DESC LIMIT 200
) ORDER BY occurrences DESC`,
    daily: `SELECT toString(toDate(ts, 'Europe/London')) AS day, harness, count() AS failures,
    uniqExact(tool_family, failure_class) AS clusters
FROM (${failures(inWindowOf("c", "ts"))})
GROUP BY day, harness ORDER BY day`,
  },
  intent: {
    kpi: `SELECT harness, tasks, labelled, corrected_n, corrected_den, frustrated_n, frustrated_den,
    ${rateOf("labelled", "tasks")} AS labelled_rate,
    ${rateOf("corrected_n", "corrected_den")} AS corrected_rate,
    ${rateOf("frustrated_n", "frustrated_den")} AS frustrated_rate
FROM (
    SELECT harness, sum(tasks) AS tasks, sum(labelled) AS labelled,
        sum(corrected_n) AS corrected_n, sum(corrected_den) AS corrected_den,
        sum(frustrated_n) AS frustrated_n, sum(frustrated_den) AS frustrated_den
    FROM (
        SELECT harness, count() AS tasks, 0 AS labelled, 0 AS corrected_n, 0 AS corrected_den,
            0 AS frustrated_n, 0 AS frustrated_den
        FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY harness
        UNION ALL
        SELECT harness, 0, sum(labelled), ${CORRECTED_N}, ${CORRECTED_DEN},
            countIf(${NEXT_PROMPT} AND sentiment_next = 'frustrated'), countIf(${NEXT_PROMPT} AND sentiment_next != '')
        FROM ${L} WHERE ${inWindow("start_ts")} GROUP BY harness
    ) GROUP BY ROLLUP(harness)
) ORDER BY harness`,
    daily: `SELECT toString(toDate(start_ts)) AS day, harness, ${CORRECTED_N} AS corrected_n,
    ${CORRECTED_DEN} AS corrected_den, ${rateOf(CORRECTED_N, CORRECTED_DEN)} AS corrected_rate
FROM ${L} WHERE ${inWindow("start_ts")} GROUP BY day, harness ORDER BY day, harness`,
    task_types: `SELECT harness, task_type, count() AS tasks
FROM ${L} WHERE ${inWindow("start_ts")} AND labelled = 1
GROUP BY harness, task_type ORDER BY harness, task_type`,
    task_type_mix: `SELECT task_type, tasks, labelled_total, ${rateOf("tasks", "labelled_total")} AS share_rate
FROM (
    SELECT task_type, count() AS tasks, sum(count()) OVER () AS labelled_total
    FROM ${L} WHERE ${inWindow("start_ts")} AND labelled = 1 GROUP BY task_type
) ORDER BY ${orderRank(TASK_TYPE_ORDER, "task_type")}, task_type`,
    correction_kinds: `SELECT harness, correction_kind_next AS correction_kind, count() AS tasks
FROM ${L} WHERE ${inWindow("start_ts")} AND ${NEXT_PROMPT} AND corrected_next = 1
GROUP BY harness, correction_kind ORDER BY harness, correction_kind`,
    correction_kind_totals: `SELECT correction_kind_next AS correction_kind, count() AS tasks
FROM ${L} WHERE ${inWindow("start_ts")} AND ${NEXT_PROMPT} AND corrected_next = 1
GROUP BY correction_kind ORDER BY correction_kind`,
    effort_payoff: `SELECT task_type, effort, count() AS tasks,
    ${CORRECTED_N} AS corrected_n, ${CORRECTED_DEN} AS corrected_den,
    ${rateOf(CORRECTED_N, CORRECTED_DEN)} AS corrected_rate,
    sumIf(clean_completion, ${NEXT_PROMPT}) AS clean_n, countIf(${NEXT_PROMPT} AND clean_completion IS NOT NULL) AS clean_den,
    ${rateOf(`sumIf(clean_completion, ${NEXT_PROMPT})`, `countIf(${NEXT_PROMPT} AND clean_completion IS NOT NULL)`)} AS clean_rate
FROM ${L} WHERE ${inWindow("start_ts")} AND labelled = 1 AND task_type != ''
GROUP BY task_type, effort
ORDER BY ${orderRank(TASK_TYPE_ORDER, "task_type")}, task_type, ${orderRank(EFFORT_ORDER, "effort")}, effort`,
    repeated: `SELECT project, correction_kind, harnesses, tasks, sessions, task_types, last_seen
FROM (${REPEATED_PAIRS}) ORDER BY tasks DESC, project, correction_kind`,
    repeated_count: `SELECT countIf(tasks >= ${REPEAT_MIN_TASKS} AND sessions >= ${REPEAT_MIN_SESSIONS}) AS repeated
FROM (${REPEATED_PAIRS})`,
    recent: `SELECT l.harness AS harness, l.session_id AS session_id, l.task_id AS task_id,
    toString(l.start_ts) AS started, l.task_type AS task_type, l.effort AS effort,
    l.correction_kind_next AS correction_kind, l.sentiment_next AS sentiment_next, p.project AS project
FROM ${L} AS l LEFT JOIN ${P} AS p ON p.session_id = l.session_id
WHERE ${inWindowOf("l", "start_ts")} AND l.harness != 'codex_exec' AND l.corrected_next = 1
    AND NOT match(p.project_root, '${EVALUATION_PATH}')
ORDER BY l.start_ts DESC LIMIT 25`,
  },
  interventions: {
    cluster_daily: `SELECT toString(toDate(ts, 'Europe/London')) AS day, harness, tool_family, failure_class,
    count() AS occurrences, uniqExact(task_id) AS tasks
FROM (${failures(inWindowOf("c", "ts"))})
GROUP BY day, harness, tool_family, failure_class ORDER BY day`,
    correction_daily: `SELECT toString(toDate(l.start_ts, 'Europe/London')) AS day, l.harness AS harness,
    p.project AS project, if(l.corrected_next = 1, l.correction_kind_next, '') AS correction_kind,
    count() AS tasks, countIf(l.corrected_next = 1) AS corrected
FROM ${L} AS l INNER JOIN ${P} AS p ON p.session_id = l.session_id
WHERE ${inWindowOf("l", "start_ts")} AND l.labelled = 1 AND p.project != ''
GROUP BY day, harness, project, correction_kind ORDER BY day`,
    task_daily: `SELECT toString(toDate(start_ts, 'Europe/London')) AS day, harness, count() AS tasks
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY day, harness ORDER BY day`,
    /**
     * One row per London day with the recurrence baseline: failure-cluster failures per
     * 100 attributed tasks and corrections per 100 labelled tasks (rates over that day's
     * sums, NULL without a denominator). Days are those with tasks or labelled tasks.
     */
    baseline_daily: `SELECT day, sum(f_n) AS failures, sum(t_n) AS tasks, sum(c_n) AS corrected, sum(l_n) AS labelled,
    if(sum(t_n) > 0, 100 * sum(f_n) / sum(t_n), NULL) AS failures_rate,
    if(sum(l_n) > 0, 100 * sum(c_n) / sum(l_n), NULL) AS corrected_rate
FROM (
    SELECT toString(toDate(ts, 'Europe/London')) AS day, count() AS f_n, toUInt64(0) AS t_n,
        toUInt64(0) AS c_n, toUInt64(0) AS l_n
    FROM (${failures(inWindowOf("c", "ts"))}) GROUP BY day
    UNION ALL
    SELECT toString(toDate(start_ts, 'Europe/London')) AS day, toUInt64(0) AS f_n, count() AS t_n,
        toUInt64(0) AS c_n, toUInt64(0) AS l_n
    FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY day
    UNION ALL
    SELECT toString(toDate(l.start_ts, 'Europe/London')) AS day, toUInt64(0) AS f_n, toUInt64(0) AS t_n,
        countIf(l.corrected_next = 1) AS c_n, count() AS l_n
    FROM ${L} AS l INNER JOIN ${P} AS p ON p.session_id = l.session_id
    WHERE ${inWindowOf("l", "start_ts")} AND l.labelled = 1 AND p.project != '' GROUP BY day
)
GROUP BY day HAVING tasks > 0 OR labelled > 0 ORDER BY day`,
  },
};

/** Queries for one session's drill-down: tasks, usage, tool calls, and signals. */
export const SESSION_QUERIES: ViewQueries = {
  tasks: `SELECT t.task_id AS task_id, toString(t.start_ts) AS started, t.duration_seconds AS duration_seconds,
    t.model AS model, t.effort AS effort, t.model_steps AS model_steps, t.output_tokens AS output_tokens,
    t.reasoning_tokens AS reasoning_tokens, t.interrupted AS interrupted, t.steered AS steered,
    t.errored AS errored, t.quick_follow_up AS quick_follow_up, t.clean_completion AS clean_completion,
    arrayStringConcat(t.friction_observed, ', ') AS observed,
    l.task_type AS task_type, l.corrected_next AS corrected_next, l.correction_kind_next AS correction_kind
FROM ${T} AS t
LEFT JOIN (
    SELECT task_id, task_type, corrected_next, correction_kind_next FROM ${L}
    WHERE harness = {harness:String} AND session_id = {session:String}
) AS l ON l.task_id = t.task_id
WHERE t.harness = {harness:String} AND t.session_id = {session:String} ORDER BY t.start_ts`,
  usage: `SELECT model, effort, count() AS operations, sum(input_tokens) AS input, sum(cached_input) AS cached,
    sum(output_tokens) AS output, sum(reasoning_tokens) AS reasoning
FROM ${U} WHERE harness = {harness:String} AND session_id = {session:String}
GROUP BY model, effort ORDER BY input DESC`,
  calls: `SELECT toString(ts) AS at, task_id, tool, outcome, exit_code, duration_ms, command_head, command_sub,
    if(trimBoth(concat(command_head, ' ', command_sub)) = '', '—', trimBoth(concat(command_head, ' ', command_sub))) AS command,
    failure_signature, arrayStringConcat(targets, ', ') AS targets
FROM ${C} WHERE harness = {harness:String} AND session_id = {session:String} ORDER BY ts LIMIT 500`,
  model_calls: `SELECT outcome, error_class, count() AS n, quantileExact(0.5)(ttft_seconds) AS ttft_p50,
    quantileExact(0.5)(duration_seconds) AS p50
FROM ${M} WHERE harness = {harness:String} AND session_id = {session:String}
GROUP BY outcome, error_class ORDER BY n DESC`,
  signals: `SELECT toString(ts) AS at, turn_id, signal, if(signal = 'prompt', '', detail) AS detail, source, tool
FROM ${S} WHERE harness = {harness:String} AND session_id = {session:String} AND signal != 'prompt'
ORDER BY ts LIMIT 500`,
  head_counts: `SELECT
    (SELECT count() FROM ${T} WHERE harness = {harness:String} AND session_id = {session:String}) AS tasks,
    (SELECT count() FROM ${C} WHERE harness = {harness:String} AND session_id = {session:String}) AS calls,
    (SELECT countIf(outcome = 'failed') FROM ${C} WHERE harness = {harness:String} AND session_id = {session:String}) AS failed,
    (SELECT count() FROM ${S} WHERE harness = {harness:String} AND session_id = {session:String} AND signal != 'prompt') AS signals`,
};
