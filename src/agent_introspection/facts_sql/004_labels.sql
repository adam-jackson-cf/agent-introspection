CREATE OR REPLACE VIEW introspection.task_labels AS
WITH labels AS (
    SELECT
        session_id,
        ts AS label_ts,
        task_type,
        task_type_confidence,
        correction,
        correction_kind,
        sentiment
    FROM introspection.prompt_labels FINAL
    WHERE status = 'labelled' AND session_id != ''
),
labelled_sessions AS (SELECT DISTINCT session_id FROM labels),
tasks AS (
    SELECT harness, session_id, task_id, start_ts, end_ts, effort, clean_completion,
        start_ts + INTERVAL 5 SECOND AS label_before
    FROM introspection.task_outcomes_snapshot
    WHERE session_id IN (SELECT session_id FROM labelled_sessions)
),
own AS (
    SELECT t.harness AS harness, t.task_id AS task_id, o.task_type AS task_type,
        o.task_type_confidence AS task_type_confidence, o.sentiment AS sentiment
    FROM tasks AS t
    ASOF INNER JOIN labels AS o ON o.session_id = t.session_id AND t.label_before >= o.label_ts
    WHERE o.label_ts >= t.start_ts - INTERVAL 5 SECOND
),
next AS (
    SELECT t.harness AS harness, t.task_id AS task_id, n.label_ts AS next_ts, n.correction AS correction,
        n.correction_kind AS correction_kind, n.sentiment AS sentiment, t.end_ts AS end_ts
    FROM tasks AS t
    ASOF INNER JOIN labels AS n ON n.session_id = t.session_id AND n.label_ts > t.end_ts
)
SELECT
    t.harness AS harness,
    t.session_id AS session_id,
    t.task_id AS task_id,
    t.start_ts AS start_ts,
    t.effort AS effort,
    t.clean_completion AS clean_completion,
    coalesce(o.task_type, '') AS task_type,
    o.task_type_confidence AS task_type_confidence,
    coalesce(o.sentiment, '') AS sentiment,
    toUInt8(o.task_id != '') AS labelled,
    toNullable(toUInt8(n.task_id != '' AND n.next_ts <= t.end_ts + INTERVAL 10 MINUTE AND n.correction >= 0.5 AND n.correction_kind NOT IN ('', 'none'))) AS corrected_next,
    if(n.task_id != '' AND n.next_ts <= t.end_ts + INTERVAL 10 MINUTE AND n.correction >= 0.5 AND n.correction_kind NOT IN ('', 'none'), n.correction_kind, '') AS correction_kind_next,
    if(n.task_id != '' AND n.next_ts <= t.end_ts + INTERVAL 10 MINUTE, n.sentiment, '') AS sentiment_next
FROM tasks AS t
LEFT JOIN own AS o ON o.harness = t.harness AND o.task_id = t.task_id
LEFT JOIN next AS n ON n.harness = t.harness AND n.task_id = t.task_id;
