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
