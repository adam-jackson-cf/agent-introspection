CREATE OR REPLACE TABLE introspection.signals
(
    signal String,
    view LowCardinality(String),
    view_title String,
    view_order UInt8,
    title String,
    question String,
    unit String,
    formula String,
    scope LowCardinality(String),
    sort UInt16
)
ENGINE = MergeTree
ORDER BY sort;

CREATE OR REPLACE TABLE introspection.signal_routes
(
    route String,
    harness LowCardinality(String),
    harness_label String,
    source LowCardinality(String),
    match String,
    expect LowCardinality(String),
    description String
)
ENGINE = MergeTree
ORDER BY (route, harness);

CREATE OR REPLACE TABLE introspection.signal_support
(
    signal String,
    harness LowCardinality(String),
    harness_label String,
    route String,
    unit String,
    alignment LowCardinality(String),
    note String
)
ENGINE = MergeTree
ORDER BY (signal, harness);

CREATE OR REPLACE TABLE introspection.signal_strays
(
    stray String,
    harness LowCardinality(String),
    source LowCardinality(String),
    match String,
    reason String
)
ENGINE = MergeTree
ORDER BY stray;

CREATE OR REPLACE TABLE introspection.signal_exclusions
(
    signal String,
    view LowCardinality(String),
    title String,
    missing String,
    reason String
)
ENGINE = MergeTree
ORDER BY signal;

CREATE OR REPLACE TABLE introspection.harnesses
(
    harness LowCardinality(String),
    label String,
    enabled UInt8
)
ENGINE = MergeTree
ORDER BY harness;
