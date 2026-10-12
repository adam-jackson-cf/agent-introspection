-- Session-to-project attribution records (sessions.py) that reached SigNoz as OTLP logs
-- from every machine. One session can have several records (rescans, a changed
-- resolution); the newest `resolved_at` wins within a refresh. `loaded_at` is the
-- record's `resolved_at`, so ReplacingMergeTree(loaded_at) keeps the newest resolution
-- however a minute or sweep refresh orders its re-reads. The family filter is
-- mandatory: `agent-introspection` also holds legacy rows with other event names and
-- non-JSON bodies.
-- Columns follow the table's order (added columns last) because backfills insert by position.
SELECT
    session_id,
    argMax(r_store, r_resolved_at) AS store,
    argMax(r_status, r_resolved_at) AS status,
    argMax(r_project_id, r_resolved_at) AS project_id,
    argMax(r_project_name, r_resolved_at) AS project_name,
    argMax(r_project_root, r_resolved_at) AS project_root,
    argMax(r_resolved_by, r_resolved_at) AS resolved_by,
    argMax(r_started_at, r_resolved_at) AS started_at,
    toDateTime64(max(r_resolved_at), 3, 'UTC') AS loaded_at,
    argMax(r_host, r_resolved_at) AS host,
    argMax(r_remote, r_resolved_at) AS remote,
    max(r_resolved_at) AS resolved_at
FROM (
    SELECT
        JSONExtractString(body, 'session_id') AS session_id,
        JSONExtractString(body, 'store') AS r_store,
        JSONExtractString(body, 'status') AS r_status,
        JSONExtractString(body, 'project_id') AS r_project_id,
        JSONExtractString(body, 'project_name') AS r_project_name,
        JSONExtractString(body, 'project_root') AS r_project_root,
        JSONExtractString(body, 'resolved_by') AS r_resolved_by,
        parseDateTime64BestEffort(JSONExtractString(body, 'started_at'), 3, 'UTC') AS r_started_at,
        resources_string['host.name'] AS r_host,
        JSONExtractString(body, 'remote') AS r_remote,
        parseDateTime64BestEffort(JSONExtractString(body, 'resolved_at'), 6, 'UTC') AS r_resolved_at
    FROM signoz_logs.distributed_logs_v2
    WHERE resources_string['service.name'] = 'agent-introspection'
        AND attributes_string['event.name'] = 'agent_introspection.session_project'
        AND JSONExtractString(body, 'schema') = 'agent-introspection.session-project/1'
        AND ({window})
)
GROUP BY session_id
