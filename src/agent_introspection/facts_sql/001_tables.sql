-- Durable, curated copies of producer telemetry. SigNoz expires traces and logs
-- after 90 days; these tables have no TTL. ReplacingMergeTree collapses the rows that
-- overlapping loader windows insert more than once, so reads use FINAL.

CREATE DATABASE IF NOT EXISTS introspection;

CREATE TABLE IF NOT EXISTS introspection.spans
(
    harness LowCardinality(String),
    ts DateTime64(9, 'UTC'),
    trace_id String,
    span_id String,
    parent_span_id String,
    name LowCardinality(String),
    duration_ns UInt64,
    has_error Bool,
    status_code LowCardinality(String),
    status_message String,
    attrs_string Map(LowCardinality(String), String),
    attrs_number Map(LowCardinality(String), Float64),
    attrs_bool Map(LowCardinality(String), Bool),
    loaded_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(loaded_at)
PARTITION BY toYYYYMM(ts)
ORDER BY (harness, trace_id, span_id);

CREATE TABLE IF NOT EXISTS introspection.logs
(
    harness LowCardinality(String),
    ts DateTime64(9, 'UTC'),
    log_id String,
    trace_id String,
    span_id String,
    event_name LowCardinality(String),
    severity LowCardinality(String),
    attrs_string Map(LowCardinality(String), String),
    attrs_number Map(LowCardinality(String), Float64),
    attrs_bool Map(LowCardinality(String), Bool),
    loaded_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(loaded_at)
PARTITION BY toYYYYMM(ts)
ORDER BY (harness, log_id);

-- Session-to-project attribution (`facts sync`, sessions.py): one row per session from
-- the harnesses' own session stores, `attributed` with its Git project or the reason
-- it has none; `resolved_by` is `workspace` (its directory's Git root) or `remote` (the
-- directory is gone; its recorded Git remote matched a repository on this machine).
-- Resolved again on a rescan; ReplacingMergeTree keeps the latest.
CREATE TABLE IF NOT EXISTS introspection.session_projects
(
    session_id String,
    store LowCardinality(String),
    status LowCardinality(String),
    project_id String,
    project_name String,
    project_root String,
    resolved_by LowCardinality(String),
    started_at DateTime64(3, 'UTC'),
    loaded_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY session_id;


-- Normalized activity-hook events (see docs/hook-events.md) that close signals the
-- producers' own telemetry lacks. Hooks keep no raw prompt, argument, or output
-- text; the sync removes each inbox file once its row is here, so this table is the
-- only copy. `producer` is the hook family (`codex` covers app-server, CLI, and exec).
CREATE TABLE IF NOT EXISTS introspection.hook_events
(
    event_id String,
    producer LowCardinality(String),
    session_id String,
    event_type LowCardinality(String),
    occurred_at DateTime64(6, 'UTC'),
    attrs_string Map(LowCardinality(String), String),
    attrs_number Map(LowCardinality(String), Float64),
    loaded_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY (session_id, event_id);

-- Jev labels for user prompts (`facts sync`, classify.py). Only the answers are
-- stored: prompt text stays in SigNoz and expires with it, so this table is the only
-- copy. A failed decision is a row with `status = 'failed'` and a reason code; it is
-- retried until `attempts` reaches the limit.
CREATE TABLE IF NOT EXISTS introspection.prompt_labels
(
    harness LowCardinality(String),
    session_id String,
    prompt_key String,
    prompt_id String,
    ts DateTime64(9, 'UTC'),
    prompt_length UInt32,
    attempts UInt8,
    status LowCardinality(String),
    reason LowCardinality(String),
    task_type LowCardinality(String) DEFAULT '',
    task_type_confidence Float32 DEFAULT 0,
    correction Float32 DEFAULT 0,
    correction_kind LowCardinality(String) DEFAULT '',
    sentiment LowCardinality(String) DEFAULT '',
    sentiment_confidence Float32 DEFAULT 0,
    classifier_model LowCardinality(String) DEFAULT '',
    classifier_version UInt16 DEFAULT 0,
    cost_usd Float64 DEFAULT 0,
    classified_at DateTime64(3, 'UTC'),
    loaded_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(classified_at)
ORDER BY (harness, session_id, prompt_key);
