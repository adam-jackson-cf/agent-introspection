import { describe, expect, test } from "bun:test";
import type { ViewResponse } from "../src/contracts";
import { createApp } from "../server";
import {
  batch,
  BRANCH_CONCURRENCY,
  batchSettled,
  type Query,
} from "./clickhouse";
import { asOfFor, refreshersFor } from "./refreshers";
import { END, HOST, registry, registryRows, START } from "./test-fixtures";
import { VIEW_QUERIES } from "./views";

describe("partial views", () => {
  const NOW = new Date("2026-09-30T12:00:10.000Z");
  const load = async (query: Query, view: string) => {
    const app = createApp({ query, workflow: () => ({}), now: () => NOW });
    const response = await app(
      new Request(
        `http://127.0.0.1:4173/api/view?view=${view}&start=${START}&end=${END}&harness=`,
        { headers: HOST },
      ),
    );
    return { response, body: (await response.json()) as ViewResponse };
  };

  test("one failing branch returns 200 with that dataset empty and named in errors", async () => {
    const failing = VIEW_QUERIES.provider!.latency!;
    const query: Query = async (sql) => {
      if (sql === failing)
        throw new Error("Code: 241. DB::Exception: Memory limit exceeded");
      return registryRows(registry(), sql);
    };
    const { response, body } = await load(query, "provider");
    expect(response.status).toBe(200);
    expect(Object.keys(body.errors)).toEqual(["latency"]);
    expect(body.errors.latency).toContain("Code: 241");
    expect(body.data.latency).toEqual([]);
    expect(Object.keys(body.data)).toEqual(
      expect.arrayContaining(Object.keys(VIEW_QUERIES.provider!)),
    );
  });

  test("a pipeline part failing leaves the rest of the view", async () => {
    const query: Query = async (sql) => {
      if (sql.includes("AS long_status")) throw new Error("Timeout exceeded");
      return registryRows(registry(), sql);
    };
    const { response, body } = await load(query, "pipeline");
    expect(response.status).toBe(200);
    expect(body.errors).toEqual({ sanitization: "Timeout exceeded" });
  });

  test("every branch failing fails the request", async () => {
    const query: Query = async (sql) => {
      if (sql.includes("introspection.signal"))
        return registryRows(registry(), sql);
      throw new Error("Code: 241. DB::Exception: Memory limit exceeded");
    };
    for (const view of ["provider", "pipeline"]) {
      const { response, body } = await load(query, view);
      expect(response.status).toBe(503);
      expect(JSON.stringify(body)).toContain("Code: 241");
      expect(body).not.toHaveProperty("data");
    }
  });
});

describe("refreshers feeding a view", () => {
  const rows = [
    { view: "load_spans", as_of: "2026-09-30 11:59:00", stale: 0 },
    { view: "load_logs", as_of: "2026-09-30 11:59:00", stale: 0 },
    {
      view: "refresh_usage_events_recent",
      as_of: "2026-09-30 11:50:00",
      stale: 0,
    },
    {
      view: "refresh_usage_events_history",
      as_of: "2026-09-30 03:00:00",
      stale: 0,
    },
    {
      view: "refresh_task_outcomes_history",
      as_of: "2026-09-29 03:00:00",
      stale: 1,
    },
  ];

  test("a failed history refresher marks only the views that read its snapshot", () => {
    const scope = (view: keyof typeof VIEW_QUERIES, snapshots = true) =>
      refreshersFor(VIEW_QUERIES[view], snapshots);
    expect(scope("cache")).toContain("refresh_usage_events_recent");
    expect(scope("cache")).not.toContain("refresh_task_outcomes_history");
    expect(asOfFor(rows, scope("cache"))).toMatchObject({
      stale: false,
      historyStale: false,
      failing: [],
    });
    expect(scope("guardrails")).toContain("refresh_tool_calls_history");
    expect(scope("guardrails")).toContain("refresh_tool_calls_recent");
    expect(scope("friction")).toEqual(
      expect.arrayContaining([
        "refresh_task_labels_history",
        "refresh_tool_calls_history",
      ]),
    );
    expect(scope("effort")).toContain("refresh_task_outcomes_history");
    expect(asOfFor(rows, scope("effort"))).toMatchObject({
      stale: false,
      historyStale: true,
      failing: ["refresh_task_outcomes_history"],
    });
    expect(asOfFor(rows, null)).toMatchObject({
      historyStale: true,
      failing: ["refresh_task_outcomes_history"],
    });
  });

  test("windows past the snapshot horizon depend on the minute loaders only", () => {
    expect(refreshersFor(VIEW_QUERIES.effort!, false)).toEqual([
      "load_spans",
      "sweep_spans",
      "load_logs",
      "sweep_logs",
    ]);
  });

  test("a view reading hook tables depends on their record loaders, even past the snapshots", () => {
    const hooks = { a: "SELECT 1 FROM introspection.hook_rows" };
    expect(refreshersFor(hooks, false)).toEqual([
      "load_spans",
      "sweep_spans",
      "load_logs",
      "sweep_logs",
      "load_hook_events",
      "sweep_hook_events",
    ]);
    expect(refreshersFor({ a: "SELECT 1" }, true)).toEqual([
      "load_spans",
      "sweep_spans",
      "load_logs",
      "sweep_logs",
    ]);
  });

  test("a failed loader or recent refresher is stale; with nothing in scope the time is unknown", () => {
    const loader = rows.map((row) =>
      row.view === "load_logs" ? { ...row, stale: 1 } : row,
    );
    expect(asOfFor(loader, ["load_logs"])).toMatchObject({
      stale: true,
      historyStale: false,
    });
    expect(asOfFor(rows, ["missing"])).toBeNull();
    const never = { view: "load_logs", as_of: null, stale: 1 };
    expect(asOfFor([never], null)).toBeNull();
    // at is the oldest loader or recent refresh; daily history refreshes are excluded.
    expect(asOfFor([never, ...rows], null)?.at).toBe("2026-09-30T11:50:00Z");
    expect(
      asOfFor(rows, ["load_spans", "refresh_usage_events_recent"])?.at,
    ).toBe("2026-09-30T11:50:00Z");
    expect(
      asOfFor(rows, ["load_spans", "refresh_usage_events_history"])?.at,
    ).toBe("2026-09-30T11:59:00Z");
  });

  test("a failing sweep is stale and named, but never lowers the as-of time", () => {
    const sweeps = [
      { view: "sweep_spans", as_of: "2026-09-30 11:00:00", stale: 0 },
      { view: "sweep_logs", as_of: "2026-09-30 11:00:00", stale: 1 },
    ];
    const withSweeps = [...rows, ...sweeps];
    const scope = ["load_spans", "sweep_spans", "load_logs", "sweep_logs"];
    expect(asOfFor(withSweeps, scope)).toEqual({
      at: "2026-09-30T11:59:00Z",
      stale: true,
      historyStale: false,
      failing: ["sweep_logs"],
    });
    expect(asOfFor(withSweeps, ["sweep_spans"])).toBeNull();
  });
});

describe("batch", () => {
  const named = {
    first: "SELECT 1 AS n",
    empty: "SELECT 1 WHERE 0",
    third: "SELECT today() AS day",
    fourth: "SELECT 4 AS n",
    fifth: "SELECT 5 AS n",
  };

  test("sends each named query as its own request, a few at a time, and keeps each result", async () => {
    let active = 0;
    let peak = 0;
    const sent: string[] = [];
    const query: Query = async (sql) => {
      sent.push(sql);
      active += 1;
      peak = Math.max(peak, active);
      await Bun.sleep(5);
      active -= 1;
      return sql === named.empty ? [] : [{ sql }];
    };
    const results = await batch(query, named);
    expect(sent.sort()).toEqual(Object.values(named).sort());
    expect(peak).toBeGreaterThan(1);
    expect(peak).toBeLessThanOrEqual(BRANCH_CONCURRENCY);
    expect(results.first).toEqual([{ sql: named.first }]);
    expect(results.empty).toEqual([]);
  });

  test("a failing branch leaves the others intact and is named with a short message", async () => {
    const query: Query = async (sql) => {
      if (sql === named.third)
        throw new Error(
          `Code: 241. DB::Exception: Memory limit\n${"x".repeat(500)}`,
        );
      return [{ ok: 1 }];
    };
    const { data, errors } = await batchSettled(query, named);
    expect(Object.keys(errors)).toEqual(["third"]);
    expect(errors.third!.length).toBeLessThanOrEqual(200);
    expect(errors.third).toContain("Code: 241");
    expect(data.third).toEqual([]);
    expect(data.first).toEqual([{ ok: 1 }]);
    await expect(batch(query, named)).rejects.toThrow("Code: 241");
  });
});
