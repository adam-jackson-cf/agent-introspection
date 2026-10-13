-- Activity-hook events (docs/hook-events.md) that reached SigNoz as OTLP logs from the
-- spool exporter on every machine. The record is the JSON `body`; the attributes are
-- flattened as the hook record defines them: numbers and flags to `attrs_number`,
-- text to `attrs_string`, every other value (lists such as targets) to compact JSON
-- text. The family filter is mandatory: `agent-introspection` also holds legacy rows
-- with other event names and non-JSON bodies, which must never be parsed here.
-- Columns follow the table's order (added columns last) because backfills insert by position.
SELECT
    JSONExtractString(body, 'event_id') AS event_id,
    JSONExtractString(body, 'producer') AS producer,
    JSONExtractString(body, 'session_id') AS session_id,
    JSONExtractString(body, 'event_type') AS event_type,
    parseDateTime64BestEffort(JSONExtractString(body, 'occurred_at'), 6, 'UTC') AS occurred_at,
    CAST(
        arrayMap(
            k -> (k, if(JSONType(body, 'attrs', k) = 'String', JSONExtractString(body, 'attrs', k), JSONExtractRaw(body, 'attrs', k))),
            arrayFilter(
                k -> JSONType(body, 'attrs', k) NOT IN ('Int64', 'UInt64', 'Double', 'Bool'),
                JSONExtractKeys(body, 'attrs')
            )
        ),
        'Map(LowCardinality(String), String)'
    ) AS attrs_string,
    CAST(
        arrayMap(
            k -> (k, if(JSONType(body, 'attrs', k) = 'Bool', toFloat64(JSONExtractBool(body, 'attrs', k)), JSONExtractFloat(body, 'attrs', k))),
            arrayFilter(
                k -> JSONType(body, 'attrs', k) IN ('Int64', 'UInt64', 'Double', 'Bool'),
                JSONExtractKeys(body, 'attrs')
            )
        ),
        'Map(LowCardinality(String), Float64)'
    ) AS attrs_number,
    now64(3) AS loaded_at,
    resources_string['host.name'] AS host
FROM signoz_logs.distributed_logs_v2
WHERE resources_string['service.name'] = 'agent-introspection'
    AND attributes_string['event.name'] = 'agent_introspection.hook_event'
    AND JSONExtractString(body, 'schema') = 'agent-introspection.hook-event/1'
    AND ({window})
