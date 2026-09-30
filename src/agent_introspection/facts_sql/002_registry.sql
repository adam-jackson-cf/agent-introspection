-- Signal support registry tables. `facts install` replaces their rows from the
-- repo-owned signal_support.toml; the dashboard renders every info note from them.

-- One row per signal definition.
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

-- One row per route and harness: the source table and predicate that identify it.
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

-- One row per signal and harness: how the producer reaches the signal.
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

-- Rows labelled with a harness that are explained rather than a live route.
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
