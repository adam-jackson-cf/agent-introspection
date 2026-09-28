-- Curated log projection. The window placeholder is replaced with the loader or backfill
-- predicate over the SigNoz source columns `timestamp` / `observed_timestamp`.
-- Raw prompt, tool argument, and tool output text is never stored. For Codex
-- tool results, normalized `x.*` fields are derived from that text instead:
-- command head/subcommand, gate-bypass flag, home-redacted targets, argument
-- hash, exit code, and a digit-normalized failure signature.
WITH
    attributes_string['event.name'] AS event,
    attributes_string['arguments'] AS args,
    attributes_string['output'] AS out,
    JSONExtractString(args, 'cmd') AS cmd,
    arrayFilter(
        t -> t != '' AND NOT match(t, '^[A-Za-z_][A-Za-z0-9_]*='),
        splitByRegexp('\\s+', cmd)
    ) AS cmd_tokens,
    if(empty(cmd_tokens), '', arrayElement(splitByChar('/', cmd_tokens[1]), -1)) AS command_head,
    if(
        length(cmd_tokens) > 1 AND match(cmd_tokens[2], '^[a-z][a-z0-9:_.-]*$'),
        cmd_tokens[2],
        ''
    ) AS command_sub,
    arrayMap(
        p -> substring(replaceRegexpOne(trimBoth(p), '^/Users/[^/]+', '~'), 1, 300),
        arrayConcat(
            -- Patches arrive with real or JSON-escaped newlines; stop at either.
            extractAll(args, '\\*\\*\\* (?:Update|Add|Delete) File: ([^\\n\\\\]+)'),
            if(JSONExtractString(args, 'path') != '', [JSONExtractString(args, 'path')], [])
        )
    ) AS targets,
    coalesce(
        nullIf(extract(out, 'Process exited with code (-?\\d+)'), ''),
        nullIf(extract(out, '^Exit code: (-?\\d+)'), '')
    ) AS exit_code,
    arrayFilter(
        l -> l != '' AND NOT match(
            l,
            '^(Chunk ID|Wall time|Process exited|Exit code|Original token count|Output:|Script completed|Script running)'
        ),
        splitByChar('\n', out)
    ) AS output_lines,
    coalesce(
        nullIf(
            arrayFirst(
                l -> match(l, '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)'),
                output_lines
            ),
            ''
        ),
        output_lines[1]
    ) AS first_output_line,
    event = 'codex.tool_result' AS is_codex_tool_result,
    is_codex_tool_result AND (
        attributes_string['success'] = 'false' OR (exit_code IS NOT NULL AND exit_code != '0')
    ) AS failed
SELECT
    resource.`service.name`::String AS harness,
    fromUnixTimestamp64Nano(toInt64(timestamp), 'UTC') AS ts,
    id AS log_id,
    trace_id,
    span_id,
    event AS event_name,
    severity_text AS severity,
    CAST(
        mapUpdate(
            CAST(
                mapFilter(
                    (k, v) -> k NOT IN (
                        'prompt', 'arguments', 'output', 'content', 'user.email',
                        'user.account_id', 'user.account_uuid', 'user.id', 'organization.id'
                    ),
                    attributes_string
                ),
                'Map(String, String)'
            ),
            if(
                is_codex_tool_result,
                map(
                    'x.command_head', command_head,
                    'x.command_sub', command_sub,
                    'x.gate_bypass', toString(match(cmd, '(--no-verify|(^|\\s)HUSKY=0|(^|\\s)SKIP=|--no-gpg-sign)')),
                    'x.workdir', replaceRegexpOne(JSONExtractString(args, 'workdir'), '^/Users/[^/]+', '~'),
                    'x.targets', toJSONString(targets),
                    'x.arguments_hash', toString(cityHash64(args)),
                    'x.failure_signature', if(
                        failed,
                        substring(
                            replaceRegexpAll(
                                replaceRegexpAll(first_output_line, '/Users/[^/ ]+', '~'),
                                '[0-9a-f]{8,}|\\d+',
                                'N'
                            ),
                            1,
                            160
                        ),
                        ''
                    )
                ),
                CAST(map(), 'Map(String, String)')
            )
        ),
        'Map(LowCardinality(String), String)'
    ) AS attrs_string,
    CAST(
        mapUpdate(
            CAST(attributes_number, 'Map(String, Float64)'),
            if(
                is_codex_tool_result,
                mapUpdate(
                    map('x.arguments_length', toFloat64(length(args)), 'x.output_length', toFloat64(length(out))),
                    if(exit_code IS NULL, CAST(map(), 'Map(String, Float64)'), map('x.exit_code', toFloat64(toInt64(assumeNotNull(exit_code)))))
                ),
                CAST(map(), 'Map(String, Float64)')
            )
        ),
        'Map(LowCardinality(String), Float64)'
    ) AS attrs_number,
    attributes_bool AS attrs_bool,
    now64(3) AS loaded_at
FROM signoz_logs.distributed_logs_v2
WHERE resource.`service.name`::String IN ('codex-app-server', 'codex_cli_rs', 'codex_exec', 'oh-my-pi', 'claude-code')
    AND ({window})
