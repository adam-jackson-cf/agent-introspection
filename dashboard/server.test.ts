import { Database } from "bun:sqlite";
import { describe, expect, test } from "bun:test";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Registry, Row, ViewResponse } from "./src/contracts";
import { createApp, parseFilters, sqliteWorkflow } from "./server";
import {
  clickhouse,
  clickhouseUrl,
  credentialsFromEnv,
  live,
  SNAPSHOT_DAYS,
  type Query,
} from "./server/clickhouse";
import {
  GENERIC_CLASS,
  HARNESS_FILTER,
  SESSION_QUERIES,
  VIEW_QUERIES,
} from "./server/views";
import { panelContribution } from "./src/components";

import {
  enabling,
  END,
  HOST,
  registry,
  registryRows,
  START,
  withThroughput,
} from "./server/test-fixtures";

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
      return registryRows(registry(), sql);
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
    const ok = clickhouse("http://localhost:8123", async (input) => {
      url = String(input);
      return new Response(JSON.stringify({ data: [{ n: 1 }] }));
    });
    expect(
      await ok("SELECT {harness:String}", { harness: "codex_exec" }),
    ).toEqual([{ n: 1 }]);
    expect(url).toContain("readonly=2");
    expect(url).toContain("param_harness=codex_exec");
    const failing = clickhouse(
      "http://localhost:8123",
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
      "https://signoz-clickhouse.orb.local:8443",
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
    const query = clickhouse(
      "http://localhost:8123",
      async (_input, requestInit) => {
        init = requestInit;
        return new Response(JSON.stringify({ data: [] }));
      },
    );
    await query("SELECT 1");
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
  });

  test("refuses non-local addresses before sending credentials", async () => {
    let called = false;
    const fetchFn = async () => {
      called = true;
      return new Response(JSON.stringify({ data: [] }));
    };
    const credentials = { user: "reader", password: "s3cret" };
    for (const remote of [
      "https://ch.example.com:8443",
      "http://10.0.0.5:8123",
      "http://127.0.0.1.evil.com:8123",
    ])
      expect(() => clickhouse(remote, fetchFn, credentials)).toThrow(
        "not local",
      );
    for (const local of [
      "http://127.0.0.1:8123",
      "http://localhost:8123",
      "http://[::1]:8123",
      "http://signoz-clickhouse.orb.local:8123",
    ])
      await clickhouse(local, fetchFn, credentials)("SELECT 1");
    expect(called).toBe(true);
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
      return registryRows(registry(), sql);
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

  test("live-preset loads in one 10-minute slot share the route count queries; the next slot counts again", async () => {
    let now = new Date("2026-09-30T12:00:10.000Z");
    const seen: string[] = [];
    const query: Query = async (sql) => {
      seen.push(sql);
      return registryRows(registry(), sql);
    };
    const app = createApp({ query, workflow: () => ({}), now: () => now });
    const load = async (end: Date) => {
      const start = new Date(end.getTime() - 7 * 86_400_000);
      const response = await app(
        new Request(
          `http://127.0.0.1:4173/api/view?view=cache&start=${start.toISOString()}&end=${end.toISOString()}&harness=`,
          { headers: HOST },
        ),
      );
      expect(response.status).toBe(200);
    };
    const countQueries = () =>
      seen.filter((sql) =>
        sql.includes("AND ts < {end:DateTime64(3, 'UTC')} GROUP BY harness"),
      ).length;

    await load(now);
    const perSlot = countQueries();
    expect(perSlot).toBeGreaterThan(0);
    now = new Date("2026-09-30T12:09:50.000Z");
    await load(now);
    expect(countQueries()).toBe(perSlot);
    now = new Date("2026-09-30T12:10:05.000Z");
    await load(now);
    expect(countQueries()).toBe(2 * perSlot);
  });

  test("a view reports its timings and the facts' freshness, loaded once per 10 minutes", async () => {
    const now = new Date("2026-09-30T12:00:10.000Z");
    let refreshes = 0;
    const query: Query = async (sql) => {
      if (sql.includes("system.view_refreshes")) {
        refreshes += 1;
        return [
          { view: "load_spans", as_of: "2026-09-30 11:58:00", stale: 1 },
          { view: "load_logs", as_of: "2026-09-30 11:57:00", stale: 0 },
        ];
      }
      return registryRows(registry(), sql);
    };
    const app = createApp({ query, workflow: () => ({}), now: () => now });
    const load = async () =>
      (await (
        await app(
          new Request(
            `http://127.0.0.1:4173/api/view?view=cache&start=${START}&end=${END}&harness=`,
            { headers: HOST },
          ),
        )
      ).json()) as ViewResponse;
    const first = await load();
    await load();
    expect(first.asOf).toEqual({
      at: "2026-09-30T11:58:00Z",
      stale: true,
      historyStale: false,
      failing: ["load_spans"],
    });
    expect(Object.keys(first.timings)).toEqual([
      "registryMs",
      "routeCountsMs",
      "sqlMs",
    ]);
    expect(refreshes).toBe(1);
  });

  test("pipeline's whole-history checks are shared for 10 minutes and a failed check is retried", async () => {
    let now = new Date("2026-09-30T12:00:10.000Z");
    let failSanitization = true;
    const seen: string[] = [];
    const query: Query = async (sql) => {
      seen.push(sql);
      if (sql.includes("AS long_status") && failSanitization) {
        failSanitization = false;
        throw new Error("Timeout exceeded");
      }
      return registryRows(registry(), sql);
    };
    const app = createApp({ query, workflow: () => ({}), now: () => now });
    const load = async () => {
      const start = new Date(now.getTime() - 7 * 86_400_000);
      const response = await app(
        new Request(
          `http://127.0.0.1:4173/api/view?view=pipeline&start=${start.toISOString()}&end=${now.toISOString()}&harness=`,
          { headers: HOST },
        ),
      );
      const body = (await response.json()) as ViewResponse;
      return { status: response.status, failed: Object.keys(body.errors) };
    };
    const runs = (marker: string) =>
      seen.filter((sql) => sql.includes(marker)).length;
    const SANITIZATION = "AS long_status";
    const UNROUTED = "ORDER BY n DESC LIMIT 40";

    expect(await load()).toEqual({ status: 200, failed: ["sanitization"] });
    now = new Date("2026-09-30T12:03:00.000Z");
    expect(await load()).toEqual({ status: 200, failed: [] });
    now = new Date("2026-09-30T12:09:50.000Z");
    expect(await load()).toEqual({ status: 200, failed: [] });
    expect(runs(SANITIZATION)).toBe(2);
    expect(runs(UNROUTED)).toBe(1);
    now = new Date("2026-09-30T12:13:30.000Z");
    expect((await load()).status).toBe(200);
    expect(runs(SANITIZATION)).toBe(3);
    expect(runs(UNROUTED)).toBe(2);
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
      proposal_events: [],
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
    event.run(
      "e4",
      4,
      "decided",
      JSON.stringify({
        actor: "adam",
        evidence: { reason: "ok to ship", prompt: "secret-prompt" },
      }),
      "2026-09-22T00:00:00Z",
    );
    db.close();

    const { findings, proposals, proposal_events } = sqliteWorkflow(path)();
    rmSync(path);
    expect(proposal_events.map((row) => row.event)).toEqual([
      "decided",
      "evaluated",
      "evaluated",
      "applied",
    ]);
    expect(proposal_events[0]).toMatchObject({
      proposal_id: "p1",
      actor: "adam",
      summary: "ok to ship",
      at: "2026-09-22T00:00:00Z",
    });
    expect(proposal_events[1]!.summary).toBe("validated");
    expect(JSON.stringify(proposal_events)).not.toContain("secret-prompt");

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
    const seen: string[] = [];
    const query: Query = async (sql) => {
      seen.push(sql);
      return registryRows(reg, sql);
    };
    const app = createApp({ query, workflow: () => ({}) });
    const served = (await (
      await app(
        new Request("http://127.0.0.1:4173/api/registry", { headers: HOST }),
      )
    ).json()) as Registry;
    expect(served.exclusions).toEqual(reg.exclusions);
    expect(
      seen.some((sql) => sql.includes("FROM introspection.signal_exclusions")),
    ).toBe(true);

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

  test("loads this machine's harnesses and derives the signals hidden here", async () => {
    const reg = enabling(withThroughput(), [
      "oh-my-pi",
      "codex_cli_rs",
      "claude-code",
    ]);
    const seen: string[] = [];
    const query: Query = async (sql) => {
      seen.push(sql);
      return registryRows(reg, sql);
    };
    const app = createApp({ query, workflow: () => ({}) });
    const served = (await (
      await app(
        new Request("http://127.0.0.1:4173/api/registry", { headers: HOST }),
      )
    ).json()) as Registry;
    expect(
      seen.some((sql) =>
        sql.includes(
          "SELECT harness, label, enabled FROM introspection.harnesses",
        ),
      ),
    ).toBe(true);
    expect(served.harnesses).toEqual(reg.harnesses);
    expect(served.hidden).toEqual([
      {
        signal: "provider.throughput",
        view: "provider",
        title: "Output throughput",
        missing: "Codex CLI",
        note: "no per-call duration",
      },
    ]);

    const pipeline = (await (
      await app(
        new Request(
          `http://127.0.0.1:4173/api/view?view=pipeline&start=${START}&end=${END}&harness=`,
          { headers: HOST },
        ),
      )
    ).json()) as { data: Record<string, Row[]> };
    expect(pipeline.data.hidden).toEqual(served.hidden as unknown as Row[]);
    const disabled = await app(
      new Request(
        `http://127.0.0.1:4173/api/view?view=cache&start=${START}&end=${END}&harness=codex_exec`,
        { headers: HOST },
      ),
    );
    expect(disabled.status).toBe(400);
    const queried: string[] = [];
    const sessionApp = createApp({
      query: async (sql) => {
        queried.push(sql);
        return registryRows(reg, sql);
      },
      workflow: () => ({}),
    });
    const before = queried.length;
    const session = await sessionApp(
      new Request(
        "http://127.0.0.1:4173/api/session?harness=codex_exec&session=s1",
        { headers: HOST },
      ),
    );
    expect(session.status).toBe(400);
    expect(
      queried.slice(before).some((sql) => sql.includes("{session:String}")),
    ).toBe(false);
  });

  test("a machine with no enabled harness is reported as not installed", async () => {
    const reg = enabling(registry(), []);
    const app = createApp({
      query: async (sql) => registryRows(reg, sql),
    });
    const response = await app(
      new Request("http://127.0.0.1:4173/api/registry", { headers: HOST }),
    );
    expect(response.status).toBe(503);
  });
});

describe("panel chips", () => {
  const chip = (state: string, rows = 0, from: string | null = null) =>
    ({ state, rows, from }) as never;

  test("rows win, with the measured signal's start day; otherwise the most telling state", () => {
    expect(
      panelContribution(
        {
          a: { "oh-my-pi": chip("rows", 3, "2026-09-24") },
          b: { "oh-my-pi": chip("rows", 9) },
        },
        ["a", "b"],
        "oh-my-pi",
      ),
    ).toEqual({ state: "rows", rows: 9, from: "2026-09-24" });
    expect(
      panelContribution(
        {
          a: { codex_exec: chip("no activity") },
          b: { codex_exec: chip("route missing") },
        },
        ["a", "b"],
        "codex_exec",
      ),
    ).toEqual({ state: "route missing", rows: 0, from: null });
    expect(panelContribution({}, ["a"], "claude-code").state).toBe(
      "not applicable",
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
    expect(VIEW_QUERIES.provider.retries).toContain(
      "event_type = 'retry' AND attrs_string['phase'] = 'start'",
    );
  });

  test("view queries read snapshots and lookup views, never the raw spans or logs", () => {
    for (const sql of all)
      expect(sql).not.toMatch(/introspection\.(spans|logs)\b/);
  });

  test("All is the union of the enabled harnesses in every windowed view query", () => {
    expect(HARNESS_FILTER).toBe(
      "harness IN (SELECT harness FROM introspection.harnesses WHERE enabled = 1) AND ({harness:String} = '' OR harness = {harness:String})",
    );
    for (const queries of Object.values(VIEW_QUERIES))
      for (const sql of Object.values(queries))
        if (sql.includes("{harness:String}"))
          expect(sql).toContain(
            "IN (SELECT harness FROM introspection.harnesses WHERE enabled = 1)",
          );
    expect(VIEW_QUERIES.intent.repeated).toContain(
      "l.harness IN (SELECT harness FROM introspection.harnesses WHERE enabled = 1)",
    );
  });

  test("signals hidden on some machines are still queried, for machines that show them", () => {
    const text = all.join("\n");
    for (const column of [
      "cache_creation_input",
      "stream_disconnect",
      "response_model",
      "sandbox_outcome",
      "tps_p50",
    ])
      expect(text).toContain(column);
  });

  test("intent queries read task_labels snapshots with bound parameters", () => {
    const intent = VIEW_QUERIES.intent;
    expect(Object.keys(intent).sort()).toEqual([
      "correction_kind_totals",
      "correction_kinds",
      "daily",
      "effort_payoff",
      "kpi",
      "recent",
      "repeated",
      "repeated_count",
      "task_type_mix",
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
      "GROUP BY tool_family, failure_class HAVING ((occurrences >= 3 AND tasks >= 2 AND days >= 2) OR (occurrences >= 5 AND tasks >= 3))",
    );
    for (const key of ["actionable", "actionable_summary"])
      expect(VIEW_QUERIES.recurrence[key]).toContain(
        `AND NOT match(failure_class, '${GENERIC_CLASS}')`,
      );
    expect(VIEW_QUERIES.tools.repeated_calls).toContain(
      "GROUP BY harness, task_id, tool, arguments_hash HAVING attempts >= 2\n    ORDER BY failed DESC, longest_run DESC, attempts DESC LIMIT 25",
    );
    expect(VIEW_QUERIES.interventions.cluster_daily).toContain(
      "GROUP BY day, harness, tool_family, failure_class",
    );
    expect(SESSION_QUERIES.tasks).toContain(
      "l.corrected_next AS corrected_next",
    );
  });

  test("failure signatures group by the full-line hash so a shared prefix does not merge", () => {
    const tools = VIEW_QUERIES.tools.signatures;
    expect(tools).toContain(
      "argMax(failure_signature, (ts, session_id)) AS signature",
    );
    expect(tools).toContain(
      "if(failure_signature_hash != '', failure_signature_hash, failure_signature) AS signature_key",
    );
    expect(tools).toContain("GROUP BY harness, tool, signature_key");
    const concentration = VIEW_QUERIES.recurrence.concentration;
    expect(concentration).toContain("any(signature) AS signature");
    expect(concentration).toContain("GROUP BY harness, signature_key, project");
    expect(concentration).toContain("GROUP BY harness, signature_key HAVING");
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
