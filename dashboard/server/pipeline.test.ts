import { describe, expect, test } from "bun:test";
import { HARNESSES, type Row } from "../src/contracts";
import { type Query } from "./clickhouse";
import {
  contributions,
  coverageGrid,
  pipelineData,
  recombine,
  routeCountQueries,
  unroutedQuery,
} from "./pipeline";
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

  test("route counts scan history before the window to date each route's first row", () => {
    const { spans } = routeCountQueries(registry());
    expect(spans).toContain("WHERE ts < {end:DateTime64(3, 'UTC')}");
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
    await pipelineData(query, registry(), {}, counts);
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
      "SELECT ts, harness, 'hooks' FROM introspection.hook_rows WHERE",
    );
  });
});
