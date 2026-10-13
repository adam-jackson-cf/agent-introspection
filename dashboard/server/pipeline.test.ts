import { describe, expect, test } from "bun:test";
import { HARNESSES, type Row } from "../src/contracts";
import { type Query } from "./clickhouse";
import {
  contributions,
  coverageGrid,
  pipelineData,
  recombine,
  routeCountQueries,
  sanitizationRows,
  unroutedQuery,
  unroutedTotalsQuery,
} from "./pipeline";
import {
  HOME_PATH_CUTOFF,
  NS_AGO,
  ORDERED_TOTALS,
  PRODUCER_PRUNE,
  SENT_FROM_TOTALS,
  spoolDelivery,
  SPOOL_STATUS,
  SPOOL_DELIVERY,
  spoolExceptions,
  withDelivery,
} from "./record-route";
import {
  counting,
  enabling,
  registry,
  withHookRoute,
  withTaskRoute,
  withThroughput,
} from "./test-fixtures";

describe("coverage grid", () => {
  test("distinguishes healthy, idle, possible break, not applicable, and strays", async () => {
    const counts = counting(
      [
        { harness: "oh-my-pi", t0: 10, u0: 10 },
        { harness: "claude-code", t0: 3, u0: 0 },
      ],
      [
        { harness: "codex-app-server", t0: 5, u0: 5 },
        { harness: "codex_cli_rs", t0: 0, u0: 0 },
      ],
    );
    const cells = Object.fromEntries(
      coverageGrid(registry(), counts).map((row) => [row.harness, row.state]),
    );
    expect(cells).toEqual({
      "oh-my-pi": "healthy",
      "codex-app-server": "healthy",
      codex_cli_rs: "idle",
      codex_exec: "idle",
      "claude-code": "stray (explained)",
    });
  });

  test("flags rows on another harness's route that no stray explains", async () => {
    const counts = counting([{ harness: "claude-code", t0: 3, u0: 2 }], []);
    const claude = coverageGrid(registry(), counts).find(
      (row) => row.harness === "claude-code",
    );
    expect(claude?.state).toBe("stray (unexplained)");
  });

  test("an emitted route without rows is a possible break only while the harness is active", async () => {
    const withTasks = withTaskRoute();
    const counts = counting(
      [{ harness: "codex_exec", t0: 0, u0: 0, t1: 0, u1: 0 }],
      [{ harness: "codex_exec", t0: 4, u0: 4 }],
      withTasks,
    );
    const exec = coverageGrid(withTasks, counts).find(
      (row) => row.harness === "codex_exec" && row.signal === "task.count",
    );
    expect(exec?.state).toBe("possible break");
  });

  test("a not-applicable cell without rows says so; with rows it is flagged", async () => {
    const idle = coverageGrid(registry(), counting([], [])).find(
      (row) => row.harness === "claude-code",
    );
    expect(idle).toMatchObject({
      state: "not applicable",
      alignment: "not applicable",
    });
    const flagged = coverageGrid(
      registry(),
      counting([{ harness: "claude-code", t0: 4, u0: 4 }], []),
    ).find((row) => row.harness === "claude-code");
    expect(flagged?.state).toBe("stray (unexplained)");
  });

  test("hook routes count from hook_rows without FINAL, keyed by event_type", async () => {
    const reg = withHookRoute();
    const queries = routeCountQueries(reg);
    expect(Object.keys(queries).sort()).toEqual(["hooks", "logs", "spans"]);
    expect(queries.hooks).toContain("FROM introspection.hook_rows WHERE");
    expect(queries.hooks).not.toContain("FINAL");
    expect(queries.hooks).toContain(
      "countIf((event_type = 'turn_stop') AND ts >= {start:DateTime64(3, 'UTC')}) AS t0",
    );
    expect(queries.spans).toContain("FROM introspection.spans FINAL");

    const counts = counting(
      [{ harness: "oh-my-pi", t0: 10, u0: 10 }],
      [],
      reg,
      [{ harness: "claude-code", t0: 7, u0: 7 }],
    );
    expect(counts.total.get("hook.turn_stop")?.get("claude-code")).toBe(7);
    const cell = coverageGrid(reg, counts).find(
      (row) =>
        row.signal === "effort.reasoning_tokens" &&
        row.harness === "claude-code",
    );
    expect(cell).toMatchObject({ state: "healthy", own: 7 });
    expect(
      contributions(reg, counts, "effort")["effort.reasoning_tokens"],
    ).toMatchObject({
      "claude-code": { state: "rows", rows: 7 },
      "oh-my-pi": { state: "rows", rows: 10 },
    });
  });

  test("unrouted rows include hook events no hook route claims", () => {
    const sql = unroutedQuery(withHookRoute());
    expect(sql).toContain(
      "toString(event_type) AS name, count() AS n\nFROM introspection.hook_rows\nWHERE",
    );
    expect(sql).toContain(
      "(harness = 'claude-code' AND (event_type = 'turn_stop'))",
    );
    expect(sql).not.toContain("hook_rows FINAL");
  });

  test("contribution chips reuse the coverage state for each enabled harness", async () => {
    const counts = counting(
      [{ harness: "oh-my-pi", t0: 10, u0: 10, f0: "" }],
      [{ harness: "codex_exec", t0: 2, u0: 2, f0: "2026-09-24" }],
    );
    expect(contributions(registry(), counts, "cache")).toEqual({
      "usage.input_tokens": {
        "oh-my-pi": { state: "rows", rows: 10, from: null },
        "codex-app-server": { state: "no activity", rows: 0, from: null },
        codex_cli_rs: { state: "no activity", rows: 0, from: null },
        codex_exec: { state: "rows", rows: 2, from: "2026-09-24" },
        "claude-code": { state: "not applicable", rows: 0, from: null },
      },
    });
  });

  test("a route with no rows while its harness is active is route missing", async () => {
    const reg = withTaskRoute();
    const counts = counting(
      [{ harness: "codex_exec", t0: 0, u0: 0, t1: 0, u1: 0 }],
      [{ harness: "codex_exec", t0: 4, u0: 4 }],
      reg,
    );
    expect(
      contributions(reg, counts, "cache")["task.count"]?.codex_exec,
    ).toEqual({ state: "route missing", rows: 0, from: null });
  });

  test("chips and coverage cover only the harnesses this machine enables", async () => {
    const reg = enabling(registry(), ["oh-my-pi", "claude-code"]);
    const counts = counting(
      [{ harness: "oh-my-pi", t0: 10, u0: 10 }],
      [{ harness: "codex_exec", t0: 2, u0: 2 }],
      reg,
    );
    expect(
      Object.keys(contributions(reg, counts, "cache")["usage.input_tokens"]!),
    ).toEqual(["oh-my-pi", "claude-code"]);
    const cells = Object.fromEntries(
      coverageGrid(reg, counts).map((row) => [row.harness, row.state]),
    );
    expect(cells).toMatchObject({
      "oh-my-pi": "healthy",
      "codex-app-server": "not in use",
      codex_exec: "not in use, rows present",
    });
  });

  test("a signal an enabled harness does not emit is hidden from chips and coverage", async () => {
    const everywhere = enabling(withThroughput(), [...HARNESSES]);
    expect(everywhere.hidden).toEqual([
      {
        signal: "provider.throughput",
        view: "provider",
        title: "Output throughput",
        missing: "Codex app-server, Codex CLI, Codex exec",
        note: "no per-call duration",
      },
    ]);
    const counts = counting([{ harness: "oh-my-pi", t0: 10, u0: 10 }], []);
    expect(contributions(everywhere, counts, "provider")).toEqual({});
    expect(
      coverageGrid(everywhere, counts).some(
        (row) => row.signal === "provider.throughput",
      ),
    ).toBe(false);

    const noCodex = enabling(withThroughput(), ["oh-my-pi", "claude-code"]);
    expect(noCodex.hidden).toEqual([]);
    expect(
      contributions(noCodex, counts, "provider")["provider.throughput"],
    ).toMatchObject({ "oh-my-pi": { state: "rows", rows: 10 } });
  });

  test("route counts scan the 90 days before the window to date each route's first row", () => {
    const { spans } = routeCountQueries(registry());
    expect(spans).toContain(
      "WHERE ts >= {start:DateTime64(3, 'UTC')} - INTERVAL 90 DAY AND ts < {end:DateTime64(3, 'UTC')}",
    );
    expect(spans).toContain(
      "countIf((name = 'chat') AND ts >= {start:DateTime64(3, 'UTC')}) AS t0",
    );
    expect(spans).toContain(
      "if(countIf((name = 'chat') AND ts < {start:DateTime64(3, 'UTC')}) = 0, toString(toDate(minIf(ts, name = 'chat'))), '') AS f0",
    );
  });
});

describe("recombination", () => {
  test("compares the direct All aggregate with the sum of per-harness aggregates", () => {
    const perHarness: Row[] = [
      {
        harness: "a",
        operations: 2,
        input: 5,
        cached: 1,
        output: 1,
        sessions: 1,
        tasks: 1,
        clean_n: 1,
        clean_den: 1,
        interrupted_n: 0,
        interrupted_den: 1,
        calls: 1,
        failed: 0,
        explicit: 1,
      },
      {
        harness: "b",
        operations: 3,
        input: 5,
        cached: 2,
        output: 1,
        sessions: 2,
        tasks: 1,
        clean_n: 0,
        clean_den: 1,
        interrupted_n: 1,
        interrupted_den: 1,
        calls: 1,
        failed: 1,
        explicit: 1,
      },
    ];
    const all: Row[] = [
      {
        operations: 5,
        input: 10,
        cached: 3,
        output: 2,
        sessions: 4,
        tasks: 2,
        clean_n: 1,
        clean_den: 2,
        interrupted_n: 1,
        interrupted_den: 2,
        calls: 2,
        failed: 1,
        explicit: 2,
      },
    ];
    const results = Object.fromEntries(
      ["usage", "tasks", "tools", "provider"].flatMap((check) => [
        [`${check}:all`, all],
        [`${check}:harness`, perHarness],
      ]),
    );
    const rows = recombine(results);
    expect(rows.find((row) => row.measure === "sessions")).toMatchObject({
      all: 4,
      sum_of_harnesses: 3,
      ok: false,
    });
    expect(
      rows.filter((row) => row.measure !== "sessions").every((row) => row.ok),
    ).toBe(true);
  });
});

describe("pipeline checks", () => {
  test("sanitization asserts hook events hold no raw-text keys, and daily rows count hooks", async () => {
    const sent: string[] = [];
    const query: Query = async (sql) => {
      sent.push(sql);
      return [];
    };
    const counts = counting([], []);
    await pipelineData(query, registry(), {}, true, {
      counts: Promise.resolve(counts),
      sanitization: sanitizationRows(query),
      unrouted: Promise.resolve({ list: [], totals: [] }),
    });
    const sql = sent.join("\n");
    expect(sql).toContain("FROM introspection.hook_events");
    for (const key of [
      "prompt",
      "user_prompt",
      "tool_input",
      "tool_response",
      "arguments",
      "output",
      "content",
      "message",
    ])
      expect(sql).toContain(`'${key}'`);
    expect(sql).toContain(
      "SELECT ts, harness, 'hooks' FROM introspection.hook_rows",
    );
  });

  const everySql = async (): Promise<string[]> => {
    const sent: string[] = [];
    const query: Query = async (sql) => {
      sent.push(sql);
      return [];
    };
    await pipelineData(query, registry(), {}, true, {
      counts: Promise.resolve(counting([], [])),
      sanitization: sanitizationRows(query),
      unrouted: Promise.resolve({ list: [], totals: [] }),
    });
    return sent;
  };

  test("every read of the agent-introspection service names its event and schema", async () => {
    for (const sql of await everySql()) {
      const reads = sql.split(
        "resources_string['service.name'] = 'agent-introspection'",
      );
      for (const read of reads.slice(1)) {
        expect(read).toContain("attributes_string['event.name'] = '");
        expect(read).toContain("JSONExtractString(body, 'schema') = '");
      }
    }
  });

  test("freshness compares hook and attribution logs with their fact tables", async () => {
    const sql = (await everySql()).join("\n");
    expect(sql).toContain(
      "SELECT 'hooks', 'agent-introspection', max(occurred_at)",
    );
    expect(sql).toContain(
      "SELECT 'attribution', 'agent-introspection', max(loaded_at)",
    );
    expect(sql).toContain("'agent_introspection.hook_event'");
    expect(sql).toContain("'agent_introspection.session_project'");
  });

  test("home paths count only rows loaded after the cutoff, on both machines' paths", async () => {
    const sql = (await everySql()).join("\n");
    expect(HOME_PATH_CUTOFF).toBe("2026-10-13 00:00:00");
    expect(sql).toContain(
      "loaded_at >= toDateTime64('2026-10-13 00:00:00', 3, 'UTC')",
    );
    expect(sql).toContain("(Users|home)/[A-Za-z0-9._-]+|/root");
    expect(sql).not.toContain("match(name, '/Users/");
    expect(sql).toContain("'signoz_hook_logs'");
  });

  test("a host silent for over 5 minutes is unknown with no backlog figures", async () => {
    const rows: Row[] = [
      {
        host: "mac",
        state: "reporting",
        pending: 0,
        rejected: 0,
        last_error_class: "",
      },
      {
        host: "nuc",
        state: "unknown",
        pending: null,
        rejected: null,
        last_error_class: "",
      },
      {
        host: "bad",
        state: "reporting",
        pending: 3,
        rejected: 2,
        last_error_class: "",
      },
    ];
    const query: Query = async (sql) =>
      sql.includes("AS last_error_class") ? rows : [];
    const { data } = await pipelineData(query, registry(), {}, true, {
      counts: Promise.resolve(counting([], [])),
      sanitization: Promise.resolve([]),
      unrouted: Promise.resolve({ list: [], totals: [] }),
    });
    expect(data.spool_exceptions!.map((row) => row.host)).toEqual([
      "nuc",
      "bad",
    ]);
    expect(data.spool_summary).toEqual([{ reporting: 2, unknown: 1 }]);
    const sql = (await everySql()).join("\n");
    expect(sql).toContain(
      "if(age_seconds > 300, 'unknown', 'reporting') AS state",
    );
    expect(sql).toContain(
      "if(state = 'unknown', NULL, JSONExtract(latest, 'pending'",
    );
  });
});

describe("spool delivery: sent counters against records received", () => {
  const status: Row[] = [
    {
      host: "mac",
      state: "reporting",
      pending: 0,
      rejected: 0,
      last_error_class: "",
    },
    {
      host: "nuc",
      state: "reporting",
      pending: 0,
      rejected: 0,
      last_error_class: "",
    },
    {
      host: "new",
      state: "reporting",
      pending: 0,
      rejected: 0,
      last_error_class: "",
    },
  ];
  const delivery: Row[] = [
    { host: "mac", family: "hook-event", sent: 500, received: 500 },
    { host: "mac", family: "session-project", sent: 10, received: 9 },
    { host: "nuc", family: "hook-event", sent: 500, received: 480 },
    { host: "nuc", family: "session-project", sent: 3, received: 3 },
  ];

  test("a host short of sent beyond the tolerance is flagged, a small lag is not", () => {
    const merged = withDelivery(status, delivery);
    expect(merged[0]).toMatchObject({
      hooks_sent: 500,
      hooks_received: 500,
      attribution_sent: 10,
      attribution_received: 9,
      delivery_gap: 0,
    });
    expect(merged[1]).toMatchObject({ hooks_received: 480, delivery_gap: 1 });
    expect(spoolExceptions(merged).map((row) => row.host)).toEqual(["nuc"]);
  });

  test("a host with no windowed heartbeat has unknown counts and no flag", () => {
    expect(withDelivery(status, delivery)[2]).toMatchObject({
      hooks_sent: null,
      attribution_received: null,
      delivery_gap: 0,
    });
  });

  test("the query orders heartbeats by their producer timestamp and windows them by collector arrival", () => {
    expect(SPOOL_DELIVERY).toContain("observed_timestamp AS observed_ns");
    expect(SPOOL_DELIVERY).toContain("timestamp AS produced_ns");
    expect(SPOOL_DELIVERY).toContain(`timestamp > ${NS_AGO(PRODUCER_PRUNE)}`);
    expect(SPOOL_DELIVERY).toContain(ORDERED_TOTALS);
    expect(SPOOL_DELIVERY).toContain(SENT_FROM_TOTALS("b.totals"));
    expect(SPOOL_DELIVERY).toContain(
      "observed_timestamp <= toUInt64(toUnixTimestamp(now() - INTERVAL 5 MINUTE))",
    );
    expect(SPOOL_DELIVERY).toContain(
      "a.observed_ns > b.first_ns AND a.observed_ns <= b.last_ns",
    );
    expect(SPOOL_DELIVERY).toContain(
      "JSONExtractInt(body, 'sent_total', family)",
    );
    expect(SPOOL_DELIVERY).not.toMatch(/timestamp AS ts\b/);
  });

  test("the status query selects and ages the latest heartbeat by collector arrival", () => {
    expect(SPOOL_STATUS).toContain("argMax(body, observed_timestamp)");
    expect(SPOOL_STATUS).toContain("max(observed_timestamp)");
    expect(SPOOL_STATUS).not.toContain("max(timestamp)");
  });

  // Evaluates the real sent arithmetic in ClickHouse over inline values(): read-only.
  const live = process.env.AGENT_INTROSPECTION_LIVE_CLICKHOUSE === "1";
  const sentFor = async (totals: number[]): Promise<number> => {
    // Rows are fed in reverse so the arrival ordering is part of what is checked.
    const rows = totals
      .map((total, index) => `(${index + 1}, ${total})`)
      .reverse()
      .join(", ");
    const sql = `SELECT ${SENT_FROM_TOTALS("totals")} AS sent FROM (SELECT ${ORDERED_TOTALS} AS totals FROM values('produced_ns UInt64, sent_total Int64', ${rows})) FORMAT TSV`;
    const proc = Bun.spawn(
      ["docker", "exec", "-i", "signoz-clickhouse", "clickhouse-client"],
      { stdin: new TextEncoder().encode(sql), stdout: "pipe", stderr: "pipe" },
    );
    const [out, code] = await Promise.all([
      new Response(proc.stdout).text(),
      proc.exited,
    ]);
    if (code !== 0) throw new Error(await new Response(proc.stderr).text());
    return Number(out.trim());
  };

  test.skipIf(!live)(
    "sent sums positive counter increments and counts a post-reset value as its own increment",
    async () => {
      expect(await sentFor([100, 0, 150])).toBe(150);
      expect(await sentFor([100, 120, 0, 30])).toBe(50);
      expect(await sentFor([10, 20, 35])).toBe(25);
      expect(await sentFor([40])).toBe(0);
    },
    120000,
  );

  // Runs SPOOL_DELIVERY's own arrived/beats logic over inline rows shaped like the
  // logs table (offsets are seconds before now). Read-only.
  const logRow = (
    kind: "beat" | "record",
    host: string,
    observedAgo: number,
    producedAgo: number,
    sentTotal = 0,
  ): string => {
    const body =
      kind === "beat"
        ? `{"schema":"agent-introspection.spool-status/1","sent_total":{"hook-event":${sentTotal},"session-project":0}}`
        : `{"schema":"agent-introspection.hook-event/1"}`;
    const event =
      kind === "beat"
        ? "agent_introspection.spool_status"
        : "agent_introspection.hook_event";
    const ns = (ago: number) =>
      `toUInt64(toUnixTimestamp(now() - INTERVAL ${ago} SECOND)) * 1000000000`;
    return `SELECT ${ns(producedAgo)} AS timestamp, ${ns(observedAgo)} AS observed_timestamp, '${body}' AS body, map('service.name', 'agent-introspection', 'host.name', '${host}') AS resources_string, map('event.name', '${event}') AS attributes_string`;
  };
  const deliveryFor = async (rows: string[]): Promise<Row[]> => {
    const sql = `${spoolDelivery(`(${rows.join(" UNION ALL ")})`)} FORMAT JSONEachRow`;
    const proc = Bun.spawn(
      ["docker", "exec", "-i", "signoz-clickhouse", "clickhouse-client"],
      { stdin: new TextEncoder().encode(sql), stdout: "pipe", stderr: "pipe" },
    );
    const [out, code] = await Promise.all([
      new Response(proc.stdout).text(),
      proc.exited,
    ]);
    if (code !== 0) throw new Error(await new Response(proc.stderr).text());
    return out
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line) as Row);
  };

  test.skipIf(!live)(
    "SPOOL_DELIVERY counts a window by arrival, keeps old-producer records, and ignores shipping order",
    async () => {
      const hour = 3600;
      // Heartbeats produced at totals 100, 130, 160; the 130 beat is stale and
      // ships last, so arrival order is 100, 160, 130. Producer order gives 60.
      const beats = [
        logRow("beat", "h", 5 * hour, 5 * hour, 100),
        logRow("beat", "h", 3 * hour, 3 * hour, 160),
        logRow("beat", "h", 2 * hour, 4 * hour, 130),
      ];
      // Within the window (arrived after the first beat, by the last beat):
      // one produced 3 days ago (past the old 48 hour guard) but observed 3.5 hours ago, one produced just
      // before arriving, one outside the window.
      const records = [
        logRow("record", "h", 3.5 * hour, 3 * 24 * hour),
        logRow("record", "h", 3.5 * hour, 3.5 * hour),
        logRow("record", "h", 6 * hour, 6 * hour),
      ];
      const rows = await deliveryFor([...beats, ...records]);
      const hook = rows.find((row) => row.family === "hook-event");
      expect(Number(hook!.sent)).toBe(60);
      expect(Number(hook!.received)).toBe(2);
    },
    120000,
  );

  test("the pipeline response carries the delivery columns", async () => {
    const query: Query = async (sql) =>
      sql.includes("AS last_error_class")
        ? status.slice(0, 2)
        : sql.includes("AS received")
          ? delivery
          : [];
    const { data } = await pipelineData(query, registry(), {}, true, {
      counts: Promise.resolve(counting([], [])),
      sanitization: Promise.resolve([]),
      unrouted: Promise.resolve({ list: [], totals: [] }),
    });
    expect(data.spool_status!.map((row) => row.delivery_gap)).toEqual([0, 1]);
    expect(data.spool_exceptions!.map((row) => row.host)).toEqual(["nuc"]);
  });
});

describe("pipeline calculations come from the server (Phase 2)", () => {
  const run = async (
    params: Record<string, string>,
    list: Row[] = [],
    totals: Row[] = [],
  ) => {
    const sent: string[] = [];
    const query: Query = async (sql) => {
      sent.push(sql);
      return [];
    };
    const counts = counting([{ harness: "claude-code", t0: 3, u0: 2 }], []);
    const { data } = await pipelineData(query, registry(), params, true, {
      counts: Promise.resolve(counts),
      sanitization: Promise.resolve([]),
      unrouted: Promise.resolve({ list, totals }),
    });
    return { data, sql: sent.join("\n;\n") };
  };

  test("coverage summary counts cells and unexplained cells after the harness filter", async () => {
    const all = await run({});
    const grid = all.data.coverage!;
    const unexplained = grid.filter((row) =>
      [
        "possible break",
        "stray (unexplained)",
        "not in use, rows present",
      ].includes(String(row.state)),
    ).length;
    expect(all.data.coverage_summary).toEqual([
      { cells: grid.length, unexplained },
    ]);
    expect(unexplained).toBeGreaterThan(0);
    const one = await run({ harness: "claude-code" });
    expect(
      one.data.coverage!.every((row) => row.harness === "claude-code"),
    ).toBe(true);
    expect(one.data.coverage_summary).toEqual([
      {
        cells: one.data.coverage!.length,
        unexplained: one.data.coverage!.filter(
          (row) => row.state === "stray (unexplained)",
        ).length,
      },
    ]);
  });

  test("the selected harness filters coverage, strays and unrouted on the server", async () => {
    const unrouted: Row[] = [
      { harness: "claude-code", source: "logs", name: "a", n: 2 },
      { harness: "oh-my-pi", source: "spans", name: "b", n: 1 },
      { harness: "", source: "hooks", name: "c", n: 4 },
    ];
    expect((await run({}, unrouted)).data.unrouted).toHaveLength(3);
    expect(
      (await run({ harness: "oh-my-pi" }, unrouted)).data.unrouted,
    ).toEqual([unrouted[1]!]);
    const strays = (await run({ harness: "oh-my-pi" })).data.strays!;
    expect(strays.every((row) => row.harness === "oh-my-pi")).toBe(true);
  });

  test("coverage states and per-view rows partition the filtered grid, and the unrouted totals are exact and filtered", async () => {
    const totals: Row[] = [
      { harness: "claude-code", names: 3, rows: 240_000 },
      { harness: "oh-my-pi", names: 1, rows: 7 },
    ];
    const { data } = await run({ harness: "claude-code" }, [], totals);
    const grid = data.coverage!;
    const states = data.coverage_states!;
    expect(states.map((row) => row.state)[0]).toBe("healthy");
    expect(states).toHaveLength(9);
    expect(states.reduce((sum, row) => sum + Number(row.cells), 0)).toBe(
      grid.length,
    );
    const byView = data.coverage_by_view!;
    expect(byView.reduce((sum, row) => sum + Number(row.cells), 0)).toBe(
      grid.length,
    );
    expect(data.unrouted_summary).toEqual([{ names: 3, rows: 240_000 }]);
    expect((await run({}, [], totals)).data.unrouted_summary).toEqual([
      { names: 4, rows: 240_007 },
    ]);
  });

  test("the unrouted totals query has no limit and the list stays capped", () => {
    expect(unroutedTotalsQuery(registry())).not.toContain("LIMIT");
    expect(unroutedQuery(registry())).toContain("LIMIT 40");
  });

  test("SQL carries the KPI totals, rates, daily sums and harness filter", async () => {
    const { sql } = await run({ harness: "oh-my-pi" });
    // Loaders KPI: total, failing, ok = total - failing.
    expect(sql).toContain("total - failing AS ok");
    // Freshness KPI: missing sources and largest lag, a NULL lag counting as 0.
    expect(sql).toContain("countIf(facts_missing = 1) AS missing_facts");
    expect(sql).toContain("if(count() > 0, max(ifNull(lag_seconds, 0)), NULL)");
    // Parity KPI and subtitle window label.
    expect(sql).toContain("countIf(ok = 1) AS matching");
    expect(sql).toContain("AS window_label");
    // Project share is an SQL rate over the attributed and tasks columns.
    expect(sql).toContain(
      "if(tasks > 0, 100 * attributed / tasks, NULL) AS share_rate",
    );
    // Daily rows are summed over sources in SQL.
    expect(sql).toContain("count() AS n FROM (");
    expect(sql).toContain("GROUP BY day, harness ORDER BY day, harness");
    // The harness filter is in the freshness, parity and project queries.
    expect(
      sql.split("{harness:String} = '' OR harness = {harness:String}").length,
    ).toBeGreaterThan(4);
    expect(sql).toContain("OR t.harness = {harness:String}");
  });

  test("sanitization totals violations per table with an All row", async () => {
    const sent: string[] = [];
    await sanitizationRows(async (sql) => {
      sent.push(sql);
      return [];
    });
    expect(sent[0]).toContain(
      "forbidden_keys + long_status + home_paths AS violations",
    );
    expect(sent[0]).toContain("GROUP BY ROLLUP(source)");
  });
});
