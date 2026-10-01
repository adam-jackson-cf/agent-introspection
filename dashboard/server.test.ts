import { Database } from "bun:sqlite";
import { describe, expect, test } from "bun:test";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Registry, Row } from "./src/contracts";
import { createApp, parseFilters, sqliteWorkflow } from "./server";
import {
  batch,
  clickhouse,
  clickhouseUrl,
  credentialsFromEnv,
  live,
  SNAPSHOT_DAYS,
  type Query,
} from "./server/clickhouse";
import {
  contributions,
  countsFromRows,
  coverageGrid,
  pipelineData,
  recombine,
  routeCountQueries,
  unroutedQuery,
} from "./server/pipeline";
import { SESSION_QUERIES, VIEW_QUERIES } from "./server/views";

const HOST = { host: "127.0.0.1:4173" };
const START = "2026-09-21T00:00:00.000Z";
const END = "2026-09-28T00:00:00.000Z";

/** A registry with one signal: omp and Codex reach it; it is not applicable to Claude Code. */
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
        alignment: "not applicable",
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
    exclusions: [
      {
        signal: "provider.throughput",
        view: "provider",
        title: "Output throughput",
        missing: "Codex app-server, Codex CLI, Codex exec",
        reason: "no per-call duration",
      },
    ],
  }) as Registry;

/** The base registry plus one activity-hook route (`hook.turn_stop`) for Claude Code. */
const withHookRoute = (): Registry => {
  const base = registry();
  return {
    ...base,
    signals: [
      ...base.signals,
      {
        ...base.signals[0]!,
        signal: "effort.reasoning_tokens",
        view: "effort",
      },
    ],
    support: [
      ...base.support,
      ...base.support.map((entry) => ({
        ...entry,
        signal: "effort.reasoning_tokens",
        ...(entry.harness === "claude-code"
          ? { route: "hook.turn_stop", alignment: "differs" as const }
          : {}),
      })),
    ],
    routes: [
      ...base.routes,
      {
        route: "hook.turn_stop",
        harness: "claude-code",
        harness_label: "Claude Code",
        source: "hooks",
        match: "event_type = 'turn_stop'",
        expect: "rows",
        description: "",
      },
    ],
  };
};

/** Route counts as `routeCountQueries` would return them per source table. */
const counting = (
  spans: Row[],
  logs: Row[],
  reg: Registry = registry(),
  hooks: Row[] = [],
) => countsFromRows(reg, { spans, logs, hooks });

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
            [
              reg.signals,
              reg.support,
              reg.routes,
              reg.strays,
              reg.exclusions,
            ].map((rows) => rows as unknown as Row[]),
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

describe("ClickHouse credentials", () => {
  test("sends basic auth from the environment and keeps readonly=2", async () => {
    let init: RequestInit | undefined;
    let url = "";
    const credentials = credentialsFromEnv({
      INTROSPECTION_CLICKHOUSE_USER: "reader",
      INTROSPECTION_CLICKHOUSE_PASSWORD: "s3cret",
    });
    const query = clickhouse(
      "https://ch.example.com:8443",
      async (input, requestInit) => {
        url = String(input);
        init = requestInit;
        return new Response(JSON.stringify({ data: [] }));
      },
      credentials,
    );
    await query("SELECT 1");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      `Basic ${btoa("reader:s3cret")}`,
    );
    expect(url).toContain("readonly=2");
    expect(credentialsFromEnv({})).toBeUndefined();
    expect(
      credentialsFromEnv({ INTROSPECTION_CLICKHOUSE_PASSWORD: "p" }),
    ).toEqual({ user: "default", password: "p" });
  });

  test("sends no Authorization header without credentials", async () => {
    let init: RequestInit | undefined;
    const query = clickhouse("http://ch:8123", async (_input, requestInit) => {
      init = requestInit;
      return new Response(JSON.stringify({ data: [] }));
    });
    await query("SELECT 1");
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
  });
});

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
    expect(queries.hooks).toContain("countIf(event_type = 'turn_stop') AS t0");
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
    ).toMatchObject({ "claude-code": 7, "oh-my-pi": 10 });
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

  test("contributions count each harness's own route and omit harnesses it is not applicable to", async () => {
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

  test("a 90-day window computed just before the request still reads the snapshots", async () => {
    const now = new Date("2026-09-30T12:00:00.000Z");
    const start = new Date(now.getTime() - SNAPSHOT_DAYS * 86_400_000 - 5_000);
    const seen: string[] = [];
    const query: Query = async (sql) => {
      seen.push(sql);
      const reg = registry();
      return sql.includes("FROM introspection.signals")
        ? batched(
            [
              reg.signals,
              reg.support,
              reg.routes,
              reg.strays,
              reg.exclusions,
            ].map((rows) => rows as unknown as Row[]),
          )
        : [];
    };
    const app = createApp({ query, workflow: () => ({}), now: () => now });
    const response = await app(
      new Request(
        `http://127.0.0.1:4173/api/view?view=cache&start=${start.toISOString()}&end=${now.toISOString()}&harness=`,
        { headers: HOST },
      ),
    );
    expect(response.status).toBe(200);
    const view = seen.filter((sql) => sql.includes("usage_events"));
    expect(view.length).toBeGreaterThan(0);
    expect(view.every((sql) => sql.includes("usage_events_snapshot"))).toBe(
      true,
    );
  });
});

describe("sqliteWorkflow", () => {
  const scratch = () =>
    join(
      tmpdir(),
      `workflow-${process.pid}-${Math.random().toString(36).slice(2)}.sqlite3`,
    );

  test("an absent store reads as empty", () => {
    expect(sqliteWorkflow(scratch())()).toEqual({
      findings: [],
      proposals: [],
    });
  });

  test("a store without the subject column reads with null subjects", () => {
    const path = scratch();
    const db = new Database(path);
    db.run(`CREATE TABLE findings (category TEXT, detector_id TEXT, trend_state TEXT,
      occurrence_count INTEGER, canonical_task_count INTEGER, local_day_count INTEGER,
      first_seen_ns INTEGER, last_seen_ns INTEGER, is_active INTEGER, id TEXT)`);
    db.run(`CREATE TABLE proposals (id TEXT, finding_id TEXT, state TEXT, payload_json TEXT,
      created_at TEXT, updated_at TEXT)`);
    db.run(
      `INSERT INTO findings VALUES ('tool', 'd', 'actionable', 3, 2, 2, 0, 0, 1, 'f1')`,
    );
    db.close();

    const { findings } = sqliteWorkflow(path)();
    rmSync(path);

    expect(findings).toHaveLength(1);
    expect(findings[0]!.harnesses).toBeNull();
    expect(findings[0]!.tool_family).toBeNull();
    expect(findings[0]!.state).toBe("actionable");
  });

  test("reads structured subjects, success metrics, and the latest evaluation", () => {
    const path = scratch();
    const db = new Database(path);
    db.run(`CREATE TABLE findings (id TEXT, category TEXT, detector_id TEXT, trend_state TEXT,
      occurrence_count INTEGER, canonical_task_count INTEGER, local_day_count INTEGER,
      first_seen_ns INTEGER, last_seen_ns INTEGER, is_active INTEGER, subject TEXT)`);
    db.run(`CREATE TABLE proposals (id TEXT, finding_id TEXT, state TEXT, payload_json TEXT,
      created_at TEXT, updated_at TEXT)`);
    db.run(`CREATE TABLE proposal_events (id TEXT, proposal_id TEXT, sequence INTEGER,
      event_type TEXT, payload_json TEXT, created_at TEXT)`);
    const cluster = JSON.stringify({
      tool_family: "shell",
      failure_class: "cmd not found",
      harnesses: ["claude-code", "oh-my-pi"],
      tools: ["Bash", "bash"],
      examples: ["x"],
      impact: 5,
    });
    const correction = JSON.stringify({
      project: "repo",
      correction_kind: "wrong_scope",
      harnesses: ["codex_cli_rs"],
      task_types: ["feature"],
      sessions: 2,
      impact: 3,
    });
    const insert = db.prepare(
      "INSERT INTO findings VALUES (?, ?, ?, 'actionable', 3, 2, 2, 0, 0, 1, ?)",
    );
    insert.run("f1", "tool_failure_cluster", "facts.failure_cluster", cluster);
    insert.run(
      "f2",
      "repeated_correction",
      "facts.repeated_correction",
      correction,
    );
    const metric = {
      metric: "cluster_task_rate",
      harnesses: ["claude-code"],
      baseline_days: 14,
      evaluation_days: 7,
      max_ratio: 0.5,
    };
    db.run(
      "INSERT INTO proposals VALUES ('p1', 'f1', 'applied', ?, '2026-09-01T00:00:00Z', '2026-09-03T00:00:00Z')",
      [
        JSON.stringify({
          intervention_type: "script",
          target: "t",
          predicted_success_metric: metric,
        }),
      ],
    );
    const event = db.prepare(
      "INSERT INTO proposal_events VALUES (?, 'p1', ?, ?, ?, ?)",
    );
    event.run("e1", 1, "applied", "{}", "2026-09-02T10:00:00Z");
    event.run(
      "e2",
      2,
      "evaluated",
      JSON.stringify({
        verdict: "regressed",
        ratio: 1.5,
        baseline_rate: 0.1,
        evaluation_rate: 0.15,
      }),
      "2026-09-20T00:00:00Z",
    );
    event.run(
      "e3",
      3,
      "evaluated",
      JSON.stringify({
        verdict: "validated",
        ratio: 0.25,
        baseline_rate: 0.2,
        evaluation_rate: 0.05,
      }),
      "2026-09-21T00:00:00Z",
    );
    db.close();

    const { findings, proposals } = sqliteWorkflow(path)();
    rmSync(path);

    expect(
      findings.find((row) => row.detector === "facts.failure_cluster"),
    ).toMatchObject({
      tool_family: "shell",
      failure_class: "cmd not found",
      harnesses: ["claude-code", "oh-my-pi"],
      tools: ["Bash", "bash"],
      impact: 5,
    });
    expect(
      findings.find((row) => row.detector === "facts.repeated_correction"),
    ).toMatchObject({
      project: "repo",
      correction_kind: "wrong_scope",
      harnesses: ["codex_cli_rs"],
      task_types: ["feature"],
      sessions: 2,
    });
    expect(proposals[0]).toMatchObject({
      tier: "script",
      metric: "cluster_task_rate",
      metric_harnesses: ["claude-code"],
      max_ratio: 0.5,
      applied_at: "2026-09-02T10:00:00Z",
      verdict: "validated",
      evaluated_ratio: 0.25,
      baseline_rate: 0.2,
      evaluation_rate: 0.05,
      tool_family: "shell",
    });
  });
});

describe("registry", () => {
  test("serves the excluded signals with the registry and in the Pipeline view", async () => {
    const reg = registry();
    const registryRows = batched(
      [reg.signals, reg.support, reg.routes, reg.strays, reg.exclusions].map(
        (rows) => rows as unknown as Row[],
      ),
    );
    const seen: string[] = [];
    const query: Query = async (sql) => {
      seen.push(sql);
      return sql.includes("FROM introspection.signals") ? registryRows : [];
    };
    const app = createApp({ query, workflow: () => ({}) });
    const served = (await (
      await app(
        new Request("http://127.0.0.1:4173/api/registry", { headers: HOST }),
      )
    ).json()) as Registry;
    expect(served.exclusions).toEqual(reg.exclusions);
    expect(seen[0]).toContain("FROM introspection.signal_exclusions");

    const view = (await (
      await app(
        new Request(
          `http://127.0.0.1:4173/api/view?view=pipeline&start=${START}&end=${END}&harness=`,
          { headers: HOST },
        ),
      )
    ).json()) as { data: Record<string, Row[]> };
    expect(view.data.exclusions).toEqual(reg.exclusions as unknown as Row[]);
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

describe("view queries", () => {
  const all = [
    ...Object.values(VIEW_QUERIES).flatMap((queries) => Object.values(queries)),
    ...Object.values(SESSION_QUERIES),
  ];

  test("hook_rows is a view and is never read with FINAL", () => {
    for (const sql of all) expect(sql).not.toMatch(/hook_rows\s+FINAL/);
    expect(VIEW_QUERIES.provider.omp_retries).toContain(
      "event_type = 'retry' AND attrs_string['phase'] = 'start'",
    );
  });

  test("excluded signals are not queried", () => {
    const text = all.join("\n");
    for (const column of [
      "cache_creation_input",
      "stream_disconnect",
      "response_model",
      "sandbox_outcome",
      "tps_p50",
    ])
      expect(text).not.toContain(column);
  });

  test("intent queries read task_labels snapshots with bound parameters", () => {
    const intent = VIEW_QUERIES.intent;
    expect(Object.keys(intent).sort()).toEqual([
      "correction_kinds",
      "daily",
      "effort_payoff",
      "kpi",
      "recent",
      "repeated",
      "task_types",
    ]);
    for (const sql of Object.values(intent)) {
      expect(sql).toContain("introspection.task_labels_snapshot");
      expect(sql).toContain("{start:DateTime64(3, 'UTC')}");
      expect(sql).toContain("{harness:String}");
    }
    // Headless Codex exec has no next prompt: out of the corrected denominator.
    expect(intent.kpi).toContain(
      "countIf(harness != 'codex_exec' AND corrected_next IS NOT NULL)",
    );
    expect(live(intent).kpi).toContain("FROM introspection.task_labels WHERE");
    expect(intent.repeated).toContain(
      "NOT match(p.project_root, '(^|\\\\W)(/private)?/(tmp|var/folders)/')",
    );
  });

  test("Claude Code reasoning comes from tasks, and failures cluster across harnesses", () => {
    expect(VIEW_QUERIES.effort.usage).toContain(
      "harness = 'claude-code' AND reasoning_tokens IS NOT NULL",
    );
    expect(VIEW_QUERIES.effort.daily_reasoning).toContain(
      "FROM introspection.task_outcomes_snapshot",
    );
    expect(VIEW_QUERIES.guardrails.bypass).not.toContain("codex%");
    expect(VIEW_QUERIES.recurrence.actionable).toContain(
      "FROM f GROUP BY tool_family, failure_class HAVING (cluster_occurrences >= 3",
    );
    expect(VIEW_QUERIES.interventions.cluster_daily).toContain(
      "GROUP BY day, harness, tool_family, failure_class",
    );
    expect(SESSION_QUERIES.tasks).toContain(
      "l.corrected_next AS corrected_next",
    );
  });
});

describe("ClickHouse address", () => {
  const config = (text: string | undefined) => () => text;

  test("the environment variable wins, then [dashboard], then the http-mode [signoz] URL", () => {
    expect(
      clickhouseUrl(
        { INTROSPECTION_CLICKHOUSE_URL: "http://127.0.0.1:1", HOME: "/h" },
        config('[dashboard]\nclickhouse_url = "http://127.0.0.1:2"'),
      ),
    ).toBe("http://127.0.0.1:1");
    expect(
      clickhouseUrl(
        { HOME: "/h" },
        config(
          '[dashboard]\nclickhouse_url = "http://127.0.0.1:2"\n[signoz]\nclickhouse_url = "http://127.0.0.1:3"',
        ),
      ),
    ).toBe("http://127.0.0.1:2");
    expect(
      clickhouseUrl(
        { HOME: "/h" },
        config('[signoz]\nclickhouse_url = "http://127.0.0.1:3"'),
      ),
    ).toBe("http://127.0.0.1:3");
  });

  test("no configured address is an error, not an assumed runtime", () => {
    expect(() =>
      clickhouseUrl(
        { HOME: "/h" },
        config('[signoz]\nclickhouse_container = "signoz-clickhouse"'),
      ),
    ).toThrow("INTROSPECTION_CLICKHOUSE_URL");
    expect(() => clickhouseUrl({ HOME: "/h" }, config(undefined))).toThrow(
      "/h/.config/agent-introspection/config.toml",
    );
  });
});
