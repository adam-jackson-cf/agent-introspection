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

-- Session-to-project events from the harness session-context hooks. The sync
-- removes each inbox file once its row is here, so this table is the only copy.
CREATE TABLE IF NOT EXISTS introspection.session_projects
(
    event_id String,
    producer LowCardinality(String),
    session_id String,
    event_type LowCardinality(String),
    occurred_at DateTime64(6, 'UTC'),
    project_id String,
    project_name String,
    project_root String,
    loaded_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY (session_id, event_id);

-- Hook events that could not name a project (for example a non-git workspace).
CREATE TABLE IF NOT EXISTS introspection.session_project_rejections
(
    rejection_id String,
    producer LowCardinality(String),
    correlation_id String,
    lifecycle_event LowCardinality(String),
    occurred_at DateTime64(6, 'UTC'),
    reason_code LowCardinality(String),
    loaded_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY rejection_id;
