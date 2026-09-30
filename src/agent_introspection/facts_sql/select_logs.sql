-- Curated log projection. The window placeholder is replaced with the loader or backfill
-- predicate over the SigNoz source columns `timestamp` / `observed_timestamp`.
-- Raw prompt, tool argument, and tool output text is never stored. For Codex
-- tool results, normalized `x.*` fields are derived from that text instead:
-- command head/subcommand, gate-bypass flag, home-redacted targets, argument
-- hash, exit code, and a digit-normalized failure signature. The command comes
-- from the `cmd` argument, or for the JavaScript `exec` tool from the first
-- nested `cmd` it passes to `exec_command`. Targets are patch file headers,
-- `path` arguments, and path-like command arguments.
WITH
    attributes_string['event.name'] AS event,
    attributes_string['arguments'] AS args,
    attributes_string['output'] AS out,
    JSONExtractString(args, 'cmd') AS json_cmd,
    if(json_cmd != '', json_cmd, extract(args, '["'']?cmd["'']?\\s*:\\s*["''`]([^"''`]+)')) AS cmd,
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
    arrayFilter(
        t -> NOT startsWith(t, '-')
            AND match(t, '^[~.]{0,2}/?[A-Za-z0-9_.@+-]+(/[A-Za-z0-9_.@+-]+)*/?$')
            AND (
                position(t, '/') > 0
                OR match(t, '\\.(md|py|ts|tsx|js|mjs|json|jsonl|toml|ya?ml|sh|sql|txt|lock|css|html|rs|go|sqlite3?|cfg|ini|csv|xml|plist|log)$')
            )
            AND NOT match(t, '^[0-9.]+$'),
        arrayMap(t -> replaceRegexpAll(t, '^["'']+|["'';,)]+$', ''), arraySlice(cmd_tokens, 2))
    ) AS command_paths,
    arraySlice(
        arrayDistinct(
            arrayMap(
                p -> substring(replaceRegexpOne(trimBoth(p), '^/Users/[^/]+', '~'), 1, 300),
                arrayConcat(
                    -- Patches arrive with real or JSON-escaped newlines; stop at either.
                    extractAll(args, '\\*\\*\\* (?:Update|Add|Delete) File: ([^\\n\\\\]+)'),
                    if(JSONExtractString(args, 'path') != '', [JSONExtractString(args, 'path')], []),
                    command_paths
                )
            )
        ),
        1,
        20
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
                    'x.gate_bypass', toString(match(args, '(--no-verify|(^|[\\s"\\x27])HUSKY=0|(^|[\\s"\\x27])SKIP=|--no-gpg-sign)')),
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
