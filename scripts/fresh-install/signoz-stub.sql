CREATE DATABASE IF NOT EXISTS signoz_traces;
CREATE DATABASE IF NOT EXISTS signoz_logs;

CREATE TABLE IF NOT EXISTS signoz_traces.distributed_signoz_index_v3
(
    timestamp DateTime64(9),
    duration_nano UInt64,
    trace_id FixedString(32),
    span_id String,
    parent_span_id String,
    name LowCardinality(String),
    has_error Bool,
    status_code_string String,
    status_message String,
    attributes_string Map(LowCardinality(String), String),
    attributes_number Map(LowCardinality(String), Float64),
    attributes_bool Map(LowCardinality(String), Bool),
    resource JSON(max_dynamic_paths = 100),
    serviceName LowCardinality(String),
    traceID FixedString(32) ALIAS trace_id,
    spanID String ALIAS span_id,
    parentSpanID String ALIAS parent_span_id
)
ENGINE = MergeTree
ORDER BY (serviceName, timestamp);

CREATE TABLE IF NOT EXISTS signoz_logs.distributed_logs_v2
(
    timestamp UInt64,
    observed_timestamp UInt64,
    id String,
    trace_id String,
    span_id String,
    severity_text LowCardinality(String),
    body String,
    attributes_string Map(LowCardinality(String), String),
    attributes_number Map(LowCardinality(String), Float64),
    attributes_bool Map(LowCardinality(String), Bool),
    resources_string Map(LowCardinality(String), String),
    resource JSON(max_dynamic_paths = 100)
)
ENGINE = MergeTree
ORDER BY timestamp;
