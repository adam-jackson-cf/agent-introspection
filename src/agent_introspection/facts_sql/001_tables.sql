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
ALTER TABLE introspection.hook_events ADD COLUMN IF NOT EXISTS host LowCardinality(String) DEFAULT '';

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
