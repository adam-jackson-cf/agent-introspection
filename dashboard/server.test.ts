import { describe, expect, test } from "bun:test";
import type { Registry, Row } from "./src/contracts";
import { createApp, parseFilters } from "./server";
import {
  batch,
  clickhouse,
  live,
  SNAPSHOT_DAYS,
  type Query,
} from "./server/clickhouse";
import {
  contributions,
  countsFromRows,
  coverageGrid,
  recombine,
} from "./server/pipeline";

const HOST = { host: "127.0.0.1:4173" };
const START = "2026-09-21T00:00:00.000Z";
const END = "2026-09-28T00:00:00.000Z";

/** A registry with one signal: omp and Codex app-server emit it, Claude Code does not. */
const registry = (): Registry =>
  ({
    signals: [
      {
        signal: "usage.input_tokens",
        view: "cache",
        view_title: "Cache efficiency",
        title: "Input tokens",
        question: "q",
        unit: "tokens",
        formula: "f",
        scope: "harness",
      },
    ],
    support: [
      {
        signal: "usage.input_tokens",
        harness: "oh-my-pi",
        harness_label: "omp",
        route: "omp.chat",
        alignment: "aligned",
        note: "",
      },
      {
        signal: "usage.input_tokens",
        harness: "codex-app-server",
        harness_label: "Codex app-server",
        route: "codex.usage",
        alignment: "aligned",
        note: "",
      },
      {
        signal: "usage.input_tokens",
        harness: "codex_cli_rs",
        harness_label: "Codex CLI",
        route: "codex.usage",
        alignment: "aligned",
        note: "",
      },
      {
        signal: "usage.input_tokens",
        harness: "codex_exec",
        harness_label: "Codex exec",
        route: "codex.usage",
        alignment: "aligned",
        note: "",
      },
      {
        signal: "usage.input_tokens",
        harness: "claude-code",
        harness_label: "Claude Code",
        route: "",
        alignment: "not emitted",
        note: "n",
      },
    ],
    routes: [
      {
        route: "omp.chat",
        harness: "oh-my-pi",
        harness_label: "omp",
        source: "spans",
        match: "name = 'chat'",
        expect: "rows",
        description: "",
      },
      ...(["codex-app-server", "codex_cli_rs", "codex_exec"] as const).map(
        (harness) => ({
          route: "codex.usage",
          harness,
          harness_label: harness,
          source: "logs" as const,
          match: "event_name = 'codex.sse_event'",
          expect: "rows" as const,
          description: "",
        }),
      ),
    ],
    strays: [
      {
        stray: "claude.chat",
        harness: "claude-code",
        source: "spans",
        match: "name = 'chat'",
        reason: "r",
      },
    ],
  }) as Registry;

/** Route counts as `routeCountQueries` would return them per source table. */
const counting = (spans: Row[], logs: Row[], reg: Registry = registry()) =>
  countsFromRows(reg, { spans, logs });

/** Rows as a batched ClickHouse request returns them: tagged and JSON-encoded. */
const batched = (results: Row[][]): Row[] =>
  results.flatMap((rows, index) =>
    rows.map((row) => ({ __q: index, __row: JSON.stringify(row) })),
  );

describe("request handling", () => {
  test("rejects untrusted hosts and non-GET methods", async () => {
    const app = createApp({ query: async () => [] });
    expect(
      (
        await app(
          new Request("http://127.0.0.1:4173/api/registry", {
            headers: { host: "evil:4173" },
          }),
        )
      ).status,
    ).toBe(403);
    expect(
      (
        await app(
          new Request("http://127.0.0.1:4173/api/registry", {
            method: "POST",
            headers: HOST,
          }),
        )
      ).status,
    ).toBe(405);
  });

  test("validates the window and harness before any query runs", () => {
    const parse = (query: string) =>
      parseFilters(new URL(`http://x/api/view?${query}`));
    expect(parse(`start=${START}&end=${END}&harness=`)).toEqual({
      start: START,
      end: END,
      harness: "",
    });
    expect(parse(`start=${END}&end=${START}`)).toBeInstanceOf(Response);
    expect(parse(`start=2026-01-01T00:00:00Z&end=${END}`)).toBeInstanceOf(
      Response,
    );
    expect(parse(`start=${START}&end=${END}&harness=codex`)).toBeInstanceOf(
      Response,
    );
  });

  test("binds the window and harness as parameters, never as SQL text", async () => {
    const seen: { sql: string; params?: Record<string, string> }[] = [];
    const query: Query = async (sql, params) => {
      seen.push({ sql, params });
      const reg = registry();
      return sql.includes("FROM introspection.signals")
        ? batched(
            [reg.signals, reg.support, reg.routes, reg.strays].map(
              (rows) => rows as unknown as Row[],
            ),
          )
        : [];
    };
    const app = createApp({ query, workflow: () => ({}) });
    const response = await app(
      new Request(
        `http://127.0.0.1:4173/api/view?view=cache&start=${START}&end=${END}&harness=oh-my-pi`,
        {
          headers: HOST,
        },
      ),
    );
    expect(response.status).toBe(200);
    const viewQueries = seen.filter((entry) =>
      entry.sql.includes("usage_events_snapshot"),
    );
    expect(viewQueries.length).toBeGreaterThan(0);
    for (const entry of viewQueries) {
      expect(entry.sql).not.toContain("oh-my-pi");
      expect(entry.params).toEqual({
        start: "2026-09-21 00:00:00.000",
        end: "2026-09-28 00:00:00.000",
        harness: "oh-my-pi",
      });
    }
  });

  test("reports an empty registry as unavailable instead of rendering without notes", async () => {
    const app = createApp({ query: async () => [] });
    const response = await app(
      new Request("http://127.0.0.1:4173/api/registry", { headers: HOST }),
    );
    expect(response.status).toBe(503);
  });
});

describe("ClickHouse client", () => {
  test("sends read-only statements with bound parameters and surfaces the exception line", async () => {
    let url = "";
    const ok = clickhouse("http://ch:8123", async (input) => {
      url = String(input);
      return new Response(JSON.stringify({ data: [{ n: 1 }] }));
    });
    expect(
      await ok("SELECT {harness:String}", { harness: "codex_exec" }),
    ).toEqual([{ n: 1 }]);
    expect(url).toContain("readonly=2");
    expect(url).toContain("param_harness=codex_exec");
    const failing = clickhouse(
      "http://ch:8123",
      async () =>
        new Response("Code: 62. DB::Exception: Syntax error\ntrace", {
          status: 500,
        }),
    );
    await expect(failing("SELECT")).rejects.toThrow(
      "Code: 62. DB::Exception: Syntax error",
    );
  });
});

describe("coverage grid", () => {
  test("distinguishes healthy, idle, possible break, not emitted, and strays", async () => {
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
    const base = registry();
    const withTasks: Registry = {
      ...base,
      signals: [...base.signals, { ...base.signals[0]!, signal: "task.count" }],
      support: [
        ...base.support,
        ...base.support.map((entry) => ({
          ...entry,
          signal: "task.count",
          route: entry.route === "codex.usage" ? "codex.turn" : entry.route,
        })),
      ],
      routes: [
        ...base.routes,
        ...(["codex-app-server", "codex_cli_rs", "codex_exec"] as const).map(
          (harness) => ({
            route: "codex.turn",
            harness,
            harness_label: harness,
            source: "spans" as const,
            match: "name = 'session_task.turn'",
            expect: "rows" as const,
            description: "",
          }),
        ),
      ],
    };
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

  test("contributions count each harness's own route and omit harnesses that do not emit", async () => {
    const counts = counting(
      [{ harness: "oh-my-pi", t0: 10, u0: 10 }],
      [{ harness: "codex_exec", t0: 2, u0: 2 }],
    );
    expect(contributions(registry(), counts, "cache")).toEqual({
      "usage.input_tokens": {
        "oh-my-pi": 10,
        "codex-app-server": 0,
        codex_cli_rs: 0,
        codex_exec: 2,
      },
    });
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

describe("batch", () => {
  test("sends named queries as one request and returns each result in order", async () => {
    const sent: string[] = [];
    const query: Query = async (sql) => {
      sent.push(sql);
      return batched([[{ n: 1 }, { n: 2 }], [], [{ day: "2026-09-28" }]]);
    };
    const results = await batch(query, {
      first: "SELECT 1 AS n",
      empty: "SELECT 1 WHERE 0",
      third: "SELECT today() AS day",
    });
    expect(sent).toHaveLength(1);
    expect(sent[0]).toContain("formatRowNoNewline('JSONEachRow', *)");
    expect(results).toEqual({
      first: [{ n: 1 }, { n: 2 }],
      empty: [],
      third: [{ day: "2026-09-28" }],
    });
  });
});

describe("snapshot horizon", () => {
  test("live queries read the fact views and the horizon matches facts.py", async () => {
    expect(
      live({ q: "SELECT 1 FROM introspection.tool_calls_snapshot AS c" }),
    ).toEqual({ q: "SELECT 1 FROM introspection.tool_calls AS c" });
    const facts = await Bun.file(
      `${import.meta.dir}/../src/agent_introspection/facts.py`,
    ).text();
    expect(facts).toContain(`SNAPSHOT_DAYS = ${SNAPSHOT_DAYS}\n`);
  });
});
