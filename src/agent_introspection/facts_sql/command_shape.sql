    -- Command shape: program, subcommand, segment count, verification kinds, and gate-run
    -- flag of the command `cmd`. Mirrors command_shape.py rule for rule; both are pinned
    -- by tests/fixtures/command_shape.json. Placeholders are filled by
    -- command_shape.sql_parts(); `cmd` is the only free name.
    -- A leading `bash -c "<string>"` is replaced by its string once.
    if(
        match(cmd, {shell_c}),
        replaceRegexpAll(extract(cmd, {shell_c}), {quotes}, ''),
        cmd
    ) AS cs_text,
    -- Segments split on && || ; | newline, as non-empty token arrays (quotes not honoured).
    arrayMap(
        s -> arrayFilter(t -> t != '', splitByRegexp({whitespace}, s)),
        splitByRegexp({separator}, cs_text)
    ) AS cs_raw,
    -- Leading environment assignments and env/exec are dropped from each segment.
    arrayFilter(
        s -> notEmpty(s),
        arrayMap(
            s -> arraySlice(
                s,
                if(
                    arrayFirstIndex(t -> NOT (match(t, {env_assignment}) OR t IN ('env', 'exec')), s) = 0,
                    length(s) + 1,
                    arrayFirstIndex(t -> NOT (match(t, {env_assignment}) OR t IN ('env', 'exec')), s)
                )
            ),
            cs_raw
        )
    ) AS cs_segs,
    -- Up to {max_unwraps} wrappers (uv run, npx, sudo, python -m, bash script.sh, ...) are shed per segment.
    arrayMap(
        s -> arrayFold((acc, step) -> {wrapper_step}, range({max_unwraps}), s),
        cs_segs
    ) AS cs_shaped,
    -- The significant segment is the first that is not a directory/environment builtin.
    if(
        empty(arrayFirst(s -> arrayElement(splitByChar('/', s[1]), -1) NOT IN ({skipped_heads}), cs_shaped)),
        cs_shaped[1],
        arrayFirst(s -> arrayElement(splitByChar('/', s[1]), -1) NOT IN ({skipped_heads}), cs_shaped)
    ) AS cs_main,
    -- Each segment as `program args...`, the string the kind and gate rules match.
    arrayMap(
        s -> concat(
            arrayElement(splitByChar('/', s[1]), -1),
            if(length(s) > 1, ' ', ''),
            arrayStringConcat(arraySlice(s, 2), ' ')
        ),
        cs_shaped
    ) AS cs_shapes,
    if(empty(cs_main), '', arrayElement(splitByChar('/', cs_main[1]), -1)) AS command_head,
    -- Only tools whose second token is a subcommand keep it, so a free-text argument
    -- (`echo word`) never becomes a stored subcommand. Mirrors command_shape.SUBCOMMAND_HEADS.
    multiIf(
        command_head IN ({script_runners}) AND cs_main[2] = 'run' AND length(cs_main) > 2,
        if(match(cs_main[3], {subcommand}), cs_main[3], ''),
        match(command_head, {python}) AND endsWith(cs_main[2], '.py'),
        if(match(arrayElement(splitByChar('/', cs_main[2]), -1), {subcommand}), arrayElement(splitByChar('/', cs_main[2]), -1), ''),
        command_head IN ({subcommand_heads}) AND match(cs_main[2], {subcommand}),
        cs_main[2],
        ''
    ) AS command_sub,
    length(cs_segs) AS segments,
    arraySort(arrayFilter(k -> k != '', {kinds})) AS verification_kinds,
    toUInt8(arrayExists(j -> {gate}, cs_shapes)) AS gate_run,
