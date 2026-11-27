    if(
        match(cmd, {shell_c}),
        replaceRegexpAll(extract(cmd, {shell_c}), {quotes}, ''),
        cmd
    ) AS cs_text,
    arrayMap(
        s -> arrayFilter(t -> t != '', splitByRegexp({whitespace}, s)),
        splitByRegexp({separator}, cs_text)
    ) AS cs_raw,
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
    arrayMap(
        s -> arrayFold((acc, step) -> {wrapper_step}, range({max_unwraps}), s),
        cs_segs
    ) AS cs_shaped,
    if(
        empty(arrayFirst(s -> arrayElement(splitByChar('/', s[1]), -1) NOT IN ({skipped_heads}), cs_shaped)),
        cs_shaped[1],
        arrayFirst(s -> arrayElement(splitByChar('/', s[1]), -1) NOT IN ({skipped_heads}), cs_shaped)
    ) AS cs_main,
    arrayMap(
        s -> concat(
            arrayElement(splitByChar('/', s[1]), -1),
            if(length(s) > 1, ' ', ''),
            arrayStringConcat(arraySlice(s, 2), ' ')
        ),
        cs_shaped
    ) AS cs_shapes,
    if(empty(cs_main), '', arrayElement(splitByChar('/', cs_main[1]), -1)) AS command_head,
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
