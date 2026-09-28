import type { ViewId } from "../src/contracts";

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

const START = "{start:DateTime64(3, 'UTC')}";
const END = "{end:DateTime64(3, 'UTC')}";
export const HARNESS_FILTER =
  "({harness:String} = '' OR harness = {harness:String})";

/** Selected window and harness over one timestamp column. */
export const inWindow = (ts: string): string =>
  `${ts} >= ${START} AND ${ts} < ${END} AND ${HARNESS_FILTER}`;

const EXPLICIT = "outcome IN ('succeeded', 'failed', 'aborted')";
/** Waiting and status-polling tools repeat identical calls by design (registry: tools.repeats). */
const NOT_POLLING =
  "tool NOT IN ('wait', 'write_stdin', 'wait_agent', 'wait_threads', 'collaborationwait_agent', 'sleep', 'get_goal', 'get_usage_limits')";
const APPROVALS = "('approved', 'approved_with_amendment', 'accept')";
const OPERATION =
  "trimBoth(multiIf(harness LIKE 'codex%', concat(tool, ' ', command_head, ' ', command_sub), harness = 'claude-code', concat(tool, ' ', command_head), tool))";
/** Seven Europe/London calendar days ending on the window end. */
const LONDON_WEEK = `toDate(ts, 'Europe/London') > toDate(${END}, 'Europe/London') - 7 AND ts < ${END} AND ${HARNESS_FILTER}`;
const ACTIONABLE =
  "(occurrences >= 3 AND tasks >= 2 AND days >= 2) OR (occurrences >= 5 AND tasks >= 3)";

const TASK_OUTCOME_COLUMNS = `
    count() AS tasks,
    sum(clean_completion) AS clean_n, count(clean_completion) AS clean_den,
    sum(interrupted) AS interrupted_n, count(interrupted) AS interrupted_den,
    sum(steered) AS steered_n, count(steered) AS steered_den,
    sum(errored) AS errored_n, count(errored) AS errored_den,
    sum(quick_follow_up) AS follow_up_n, count(quick_follow_up) AS follow_up_den`;

export const VIEW_QUERIES: Record<Exclude<ViewId, "pipeline">, ViewQueries> = {
  cache: {
    kpi: `SELECT harness, sum(input_tokens) AS input, sum(cached_input) AS cached,
    sum(coalesce(cache_creation_input, 0)) AS creation, sum(output_tokens) AS output,
    count() AS operations, uniqExact(session_id) AS sessions
FROM ${U} WHERE ${inWindow("ts")} GROUP BY harness ORDER BY harness`,
    daily: `SELECT toString(toDate(ts)) AS day, harness, sum(input_tokens) AS input,
    sum(cached_input) AS cached, sum(output_tokens) AS output
FROM ${U} WHERE ${inWindow("ts")} GROUP BY day, harness ORDER BY day, harness`,
    models: `SELECT harness, provider, model, count() AS operations, sum(input_tokens) AS input,
    sum(cached_input) AS cached, sum(coalesce(cache_creation_input, 0)) AS creation, sum(output_tokens) AS output
FROM ${U} WHERE ${inWindow("ts")} GROUP BY harness, provider, model ORDER BY input DESC LIMIT 40`,
    sessions: `SELECT harness, session_id, arrayStringConcat(arraySort(groupUniqArray(model)), ', ') AS models,
    count() AS operations, sum(input_tokens) AS input, sum(cached_input) AS cached, sum(output_tokens) AS output,
    toString(min(ts)) AS first_seen, toString(max(ts)) AS last_seen
FROM ${U} WHERE ${inWindow("ts")} GROUP BY harness, session_id
ORDER BY input - cached DESC LIMIT 20`,
  },
  effort: {
    usage: `SELECT harness, effort, count() AS calls, sum(coalesce(reasoning_tokens, 0)) AS reasoning,
    sum(output_tokens) AS output
FROM ${U} WHERE ${inWindow("ts")} AND reasoning_tokens IS NOT NULL GROUP BY harness, effort
ORDER BY harness, effort`,
    outcomes: `SELECT harness, effort, ${TASK_OUTCOME_COLUMNS},
    countIf(effort IN ('high', 'xhigh')) AS heavy,
    quantileExact(0.5)(duration_seconds) AS p50_duration, quantileExact(0.9)(duration_seconds) AS p90_duration,
    sum(model_steps) AS steps, sum(delegations) AS delegations,
    sum(coalesce(reasoning_tokens, 0)) AS reasoning, count(reasoning_tokens) AS reasoning_den,
    sum(coalesce(output_tokens, 0)) AS output
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY harness, effort ORDER BY harness, effort`,
    daily_tasks: `SELECT toString(toDate(start_ts)) AS day, effort, count() AS tasks,
    sum(clean_completion) AS clean_n, count(clean_completion) AS clean_den,
    quantileExact(0.5)(duration_seconds) AS p50_duration, avg(model_steps) AS avg_steps
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY day, effort ORDER BY day, effort`,
    daily_reasoning: `SELECT toString(toDate(ts)) AS day, effort, sum(coalesce(reasoning_tokens, 0)) AS reasoning
FROM ${U} WHERE ${inWindow("ts")} AND reasoning_tokens IS NOT NULL GROUP BY day, effort ORDER BY day, effort`,
    model_effort: `SELECT harness, model, effort, count() AS tasks,
    sum(clean_completion) AS clean_n, count(clean_completion) AS clean_den,
    quantileExact(0.5)(duration_seconds) AS p50_duration, avg(model_steps) AS avg_steps,
    sum(coalesce(reasoning_tokens, 0)) AS reasoning, count(reasoning_tokens) AS reasoning_den
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY harness, model, effort HAVING tasks >= 3
ORDER BY harness, model, effort`,
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
    uniqExactIf(task_id, task_id != '' AND outcome = 'failed') AS failed_tasks
FROM ${C} WHERE ${inWindow("ts")} GROUP BY harness ORDER BY harness`,
    daily: `SELECT toString(toDate(ts)) AS day, harness, count() AS calls,
    countIf(${EXPLICIT}) AS explicit, countIf(outcome = 'failed') AS failed
FROM ${C} WHERE ${inWindow("ts")} GROUP BY day, harness ORDER BY day, harness`,
    by_tool: `SELECT harness, tool, count() AS calls, countIf(${EXPLICIT}) AS explicit,
    countIf(outcome = 'failed') AS failed,
    uniqExactIf(task_id, task_id != '' AND outcome = 'failed') AS failed_tasks
FROM ${C} WHERE ${inWindow("ts")} GROUP BY harness, tool ORDER BY failed DESC, calls DESC LIMIT 30`,
    signatures: `SELECT harness, tool, failure_signature AS signature, count() AS occurrences,
    uniqExactIf(task_id, task_id != '') AS tasks, uniqExact(session_id) AS sessions,
    toString(max(ts)) AS last_seen, any(session_id) AS example_session
FROM ${C} WHERE ${inWindow("ts")} AND outcome = 'failed' AND failure_signature != ''
GROUP BY harness, tool, signature ORDER BY occurrences DESC LIMIT 30`,
    repeat_summary: `SELECT harness, uniqExactIf(task_id, attempts >= 2) AS repeat_tasks, uniqExact(task_id) AS tasks,
    sumIf(attempts, attempts >= 2) AS repeated_attempts
FROM (
    SELECT harness, task_id, arguments_hash, count() AS attempts
    FROM ${C} WHERE ${inWindow("ts")} AND task_id != '' AND arguments_hash != '' AND ${NOT_POLLING}
    GROUP BY harness, task_id, arguments_hash
) GROUP BY harness ORDER BY harness`,
    repeats: `SELECT harness, session_id, task_id, tool, any(command_head) AS command, count() AS attempts,
    countIf(outcome = 'failed') AS failed
FROM ${C} WHERE ${inWindow("ts")} AND task_id != '' AND arguments_hash != '' AND ${NOT_POLLING}
GROUP BY harness, session_id, task_id, tool, arguments_hash HAVING attempts >= 2
ORDER BY attempts DESC LIMIT 25`,
    loops: `WITH
    ordered AS (
        SELECT harness, session_id, task_id, ts, tool, command_head, arguments_hash, outcome,
            lagInFrame(arguments_hash, 1, '') OVER (
                PARTITION BY harness, task_id ORDER BY ts ROWS BETWEEN 1 PRECEDING AND CURRENT ROW
            ) AS previous
        FROM ${C} WHERE ${inWindow("ts")} AND task_id != '' AND arguments_hash != '' AND ${NOT_POLLING}
    ),
    runs AS (
        SELECT *, sum(toUInt32(arguments_hash != previous)) OVER (
            PARTITION BY harness, task_id ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS run
        FROM ordered
    )
SELECT harness, session_id, task_id, any(tool) AS tool, any(command_head) AS command, count() AS length,
    countIf(outcome = 'failed') AS failed, toString(min(ts)) AS started
FROM runs GROUP BY harness, session_id, task_id, run HAVING length >= 3
ORDER BY length DESC LIMIT 25`,
  },
  friction: {
    kpi: `SELECT harness, ${TASK_OUTCOME_COLUMNS},
    arrayStringConcat(any(friction_observed), ', ') AS observed
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY harness ORDER BY harness`,
    daily: `SELECT toString(toDate(start_ts)) AS day, harness, ${TASK_OUTCOME_COLUMNS}
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY day, harness ORDER BY day, harness`,
    recovery_summary: `WITH ops AS (
    SELECT harness, task_id, ${OPERATION} AS operation,
        minIf(ts, outcome = 'failed') AS first_failure,
        groupArrayIf(ts, outcome = 'succeeded') AS successes
    FROM ${C} WHERE ${inWindow("ts")} AND task_id != '' AND outcome IN ('failed', 'succeeded')
    GROUP BY harness, task_id, operation HAVING countIf(outcome = 'failed') > 0
)
SELECT harness, count() AS failed_ops,
    countIf(arrayExists(t -> t > first_failure, successes)) AS recovered,
    quantileExactIf(0.5)(
        dateDiff('millisecond', first_failure, arrayMin(arrayFilter(t -> t > first_failure, successes))) / 1000,
        arrayExists(t -> t > first_failure, successes)
    ) AS p50_recovery_seconds
FROM ops GROUP BY harness ORDER BY harness`,
    recovery_ops: `WITH ops AS (
    SELECT harness, task_id, ${OPERATION} AS operation,
        minIf(ts, outcome = 'failed') AS first_failure,
        groupArrayIf(ts, outcome = 'succeeded') AS successes
    FROM ${C} WHERE ${inWindow("ts")} AND task_id != '' AND outcome IN ('failed', 'succeeded')
    GROUP BY harness, task_id, operation HAVING countIf(outcome = 'failed') > 0
)
SELECT harness, operation, count() AS failed_ops,
    countIf(arrayExists(t -> t > first_failure, successes)) AS recovered
FROM ops GROUP BY harness, operation ORDER BY failed_ops DESC LIMIT 25`,
    signals_daily: `SELECT toString(toDate(ts)) AS day, harness, signal, count() AS n
FROM ${S} WHERE ${inWindow("ts")} AND signal IN ('interrupt', 'steer')
GROUP BY day, harness, signal ORDER BY day`,
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
    decisions_daily: `SELECT toString(toDate(ts)) AS day, harness, count() AS decisions,
    countIf(detail NOT IN ${APPROVALS}) AS rejected
FROM ${S} WHERE ${inWindow("ts")} AND signal = 'tool_decision' GROUP BY day, harness ORDER BY day`,
    sandbox: `SELECT harness, detail AS outcome, tool, count() AS n
FROM ${S} WHERE ${inWindow("ts")} AND signal = 'sandbox_outcome'
GROUP BY harness, outcome, tool ORDER BY n DESC LIMIT 30`,
    sandbox_daily: `SELECT toString(toDate(ts)) AS day, harness, count() AS outcomes,
    countIf(detail = 'denied') AS denied
FROM ${S} WHERE ${inWindow("ts")} AND signal = 'sandbox_outcome' GROUP BY day, harness ORDER BY day`,
    bypass: `SELECT harness, countIf(command_head != '') AS commands, countIf(gate_bypass = 1) AS bypass,
    uniqExactIf(task_id, gate_bypass = 1 AND task_id != '') AS tasks
FROM ${C} WHERE ${inWindow("ts")} AND harness LIKE 'codex%' GROUP BY harness ORDER BY harness`,
    bypass_calls: `SELECT toString(ts) AS at, harness, session_id, task_id, command_head, command_sub, outcome
FROM ${C} WHERE ${inWindow("ts")} AND gate_bypass = 1 ORDER BY ts DESC LIMIT 25`,
    churn_summary: `SELECT harness, uniqExact(task_id) AS tasks, count() AS pairs FROM (
    SELECT harness, task_id, target,
        uniqExact(concat(tool, ' ', command_head, ' ', command_sub)) AS commands
    FROM ${C} ARRAY JOIN targets AS target
    WHERE ${inWindow("ts")} AND task_id != ''
    GROUP BY harness, task_id, target HAVING commands >= 3
) GROUP BY harness ORDER BY harness`,
    churn: `SELECT harness, session_id, task_id, target,
    uniqExact(concat(tool, ' ', command_head, ' ', command_sub)) AS commands, count() AS calls,
    countIf(outcome = 'failed') AS failed
FROM ${C} ARRAY JOIN targets AS target
WHERE ${inWindow("ts")} AND task_id != ''
GROUP BY harness, session_id, task_id, target HAVING commands >= 3
ORDER BY commands DESC, calls DESC LIMIT 25`,
  },
  provider: {
    kpi: `SELECT harness, count() AS calls, countIf(outcome = 'failed') AS failed,
    countIf(outcome = 'unknown') AS unknown,
    countIf(stream_disconnect = 1) AS disconnects, count(stream_disconnect) AS disconnect_den,
    countIf(response_model != '' AND response_model != model) AS mismatched,
    countIf(response_model != '') AS with_response_model,
    countIf(attempt > 1) AS retried, count(attempt) AS attempt_den
FROM ${M} WHERE ${inWindow("ts")} GROUP BY harness ORDER BY harness`,
    latency: `SELECT harness, model, count() AS calls,
    quantileExact(0.5)(duration_seconds) AS p50, quantileExact(0.95)(duration_seconds) AS p95,
    quantileExact(0.5)(ttft_seconds) AS ttft_p50, quantileExact(0.95)(ttft_seconds) AS ttft_p95,
    quantileExact(0.5)(if(duration_seconds > ttft_seconds AND output_tokens > 0,
        output_tokens / (duration_seconds - ttft_seconds), NULL)) AS tps_p50
FROM ${M} WHERE ${inWindow("ts")} GROUP BY harness, model ORDER BY calls DESC LIMIT 30`,
    codex_sampling: `SELECT harness, attrs_string['model'] AS model,
    countIf(name = 'run_sampling_request') AS steps,
    countIf(name = 'try_run_sampling_request') AS attempts,
    quantileExactIf(0.5)(duration_ns / 1e9, name = 'run_sampling_request') AS p50,
    quantileExactIf(0.95)(duration_ns / 1e9, name = 'run_sampling_request') AS p95
FROM introspection.spans FINAL
WHERE ${inWindow("ts")} AND harness LIKE 'codex%' AND name IN ('run_sampling_request', 'try_run_sampling_request')
GROUP BY harness, model ORDER BY steps DESC`,
    daily: `SELECT toString(toDate(ts)) AS day, harness, count() AS calls,
    countIf(outcome = 'failed') AS failed, quantileExact(0.5)(ttft_seconds) AS ttft_p50
FROM ${M} WHERE ${inWindow("ts")} GROUP BY day, harness ORDER BY day, harness`,
    errors: `SELECT harness, model, error_class, count() AS n, toString(max(ts)) AS last_seen
FROM ${M} WHERE ${inWindow("ts")} AND outcome = 'failed'
GROUP BY harness, model, error_class ORDER BY n DESC LIMIT 25`,
    claude_retries: `SELECT harness, countIf(event_name = 'api_error') AS failed_attempts,
    countIf(event_name = 'api_retries_exhausted') AS exhausted
FROM introspection.logs FINAL
WHERE ${inWindow("ts")} AND harness = 'claude-code' AND event_name IN ('api_error', 'api_retries_exhausted')
GROUP BY harness`,
  },
  recurrence: {
    targets: `SELECT target, arrayStringConcat(arraySort(groupUniqArray(harness)), ', ') AS harnesses,
    uniqExact(harness, task_id) AS tasks, uniqExact(toDate(ts, 'Europe/London')) AS days,
    count() AS calls, countIf(outcome = 'failed') AS failed
FROM ${C} ARRAY JOIN targets AS target
WHERE ${inWindow("ts")} AND task_id != ''
GROUP BY target HAVING tasks >= 2 ORDER BY tasks DESC, calls DESC LIMIT 30`,
    target_summary: `SELECT harness, uniqExact(target) AS targets, uniqExactIf(target, tasks >= 2) AS recurring
FROM (
    SELECT harness, target, uniqExact(task_id) AS tasks
    FROM ${C} ARRAY JOIN targets AS target
    WHERE ${inWindow("ts")} AND task_id != '' GROUP BY harness, target
) GROUP BY harness ORDER BY harness`,
    signatures: `SELECT harness, tool, failure_signature AS signature, count() AS occurrences,
    uniqExact(task_id) AS tasks, uniqExact(toDate(ts, 'Europe/London')) AS days,
    toString(min(ts)) AS first_seen, toString(max(ts)) AS last_seen
FROM ${C} WHERE ${inWindow("ts")} AND outcome = 'failed' AND failure_signature != '' AND task_id != ''
GROUP BY harness, tool, signature HAVING tasks >= 2 ORDER BY tasks DESC, occurrences DESC LIMIT 30`,
    actionable: `SELECT harness, tool, failure_signature AS signature, count() AS occurrences,
    uniqExact(task_id) AS tasks, uniqExact(toDate(ts, 'Europe/London')) AS days,
    toString(max(ts)) AS last_seen
FROM ${C} WHERE ${LONDON_WEEK} AND outcome = 'failed' AND failure_signature != '' AND task_id != ''
GROUP BY harness, tool, signature HAVING ${ACTIONABLE} ORDER BY occurrences DESC LIMIT 30`,
    concentration: `SELECT harness, signature, sum(n) AS occurrences, sumIf(n, workdir != '') AS attributed,
    maxIf(n, workdir != '') AS top_count, argMaxIf(workdir, n, workdir != '') AS top_workdir,
    uniqExactIf(workdir, workdir != '') AS workdirs
FROM (
    SELECT harness, failure_signature AS signature, workdir, count() AS n
    FROM ${C} WHERE ${inWindow("ts")} AND outcome = 'failed' AND failure_signature != ''
    GROUP BY harness, signature, workdir
) GROUP BY harness, signature HAVING attributed > 0 AND occurrences >= 3
ORDER BY occurrences DESC LIMIT 25`,
    daily: `SELECT toString(toDate(ts, 'Europe/London')) AS day, harness, count() AS failures,
    uniqExact(failure_signature) AS signatures
FROM ${C} WHERE ${inWindow("ts")} AND outcome = 'failed' AND failure_signature != ''
GROUP BY day, harness ORDER BY day`,
  },
  interventions: {
    signature_daily: `SELECT toString(toDate(ts, 'Europe/London')) AS day, harness, failure_signature AS signature,
    count() AS occurrences, uniqExact(task_id) AS tasks
FROM ${C} WHERE ${inWindow("ts")} AND outcome = 'failed' AND failure_signature != '' AND task_id != ''
GROUP BY day, harness, signature ORDER BY day`,
    task_daily: `SELECT toString(toDate(start_ts, 'Europe/London')) AS day, harness, count() AS tasks
FROM ${T} WHERE ${inWindow("start_ts")} GROUP BY day, harness ORDER BY day`,
  },
};

/** Queries for one session's drill-down: tasks, usage, tool calls, and signals. */
export const SESSION_QUERIES: ViewQueries = {
  tasks: `SELECT task_id, toString(start_ts) AS started, duration_seconds, model, effort, model_steps,
    output_tokens, reasoning_tokens, interrupted, steered, errored, quick_follow_up, clean_completion,
    arrayStringConcat(friction_observed, ', ') AS observed
FROM ${T} WHERE harness = {harness:String} AND session_id = {session:String} ORDER BY start_ts`,
  usage: `SELECT model, effort, count() AS operations, sum(input_tokens) AS input, sum(cached_input) AS cached,
    sum(output_tokens) AS output, sum(reasoning_tokens) AS reasoning
FROM ${U} WHERE harness = {harness:String} AND session_id = {session:String}
GROUP BY model, effort ORDER BY input DESC`,
  calls: `SELECT toString(ts) AS at, task_id, tool, outcome, exit_code, duration_ms, command_head, command_sub,
    failure_signature, arrayStringConcat(targets, ', ') AS targets
FROM ${C} WHERE harness = {harness:String} AND session_id = {session:String} ORDER BY ts LIMIT 500`,
  model_calls: `SELECT outcome, error_class, count() AS n, quantileExact(0.5)(ttft_seconds) AS ttft_p50,
    quantileExact(0.5)(duration_seconds) AS p50
FROM ${M} WHERE harness = {harness:String} AND session_id = {session:String}
GROUP BY outcome, error_class ORDER BY n DESC`,
  signals: `SELECT toString(ts) AS at, turn_id, signal, if(signal = 'prompt', '', detail) AS detail, source, tool
FROM ${S} WHERE harness = {harness:String} AND session_id = {session:String} AND signal != 'prompt'
ORDER BY ts LIMIT 500`,
};
