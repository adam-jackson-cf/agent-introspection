import { describe, expect, test } from "bun:test";
import { watch } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createApp, runPipeline } from "./server";
import type {
  PipelineReport,
  RouteDefinition,
  RouteId,
  WidgetResult,
} from "./src/contracts";

const route: RouteDefinition = {
  id: "pipeline",
  path: "/pipeline",
  title: "Pipeline",
  eyebrow: "01",
  subtitle: "Test",
};
const widgets: Record<RouteId, WidgetResult[]> = {
  pipeline: [],
  provider: [],
  usage: [],
  tools: [],
  recurrence: [],
};
const implementation = {
  calculationId: "agent-introspection.pipeline-dashboard" as const,
  calculationSha256: "a".repeat(64),
  deployments: [
    {
      fingerprint: "b".repeat(64),
      projectionId: "agent-introspection.pipeline-observations" as const,
      projectionSha256: "c".repeat(64),
    },
  ],
};
const pipelineReport: PipelineReport = {
  start: "2026-09-01T00:00:00Z",
  end: "2026-09-02T00:00:00Z",
  evaluatedAt: "2026-09-02T00:00:01Z",
  implementation,
  panels: {},
};
const measuredWidget: WidgetResult = {
  widget: {
    id: "p1-snapshot",
    title: "P1",
    measureIds: ["P1"],
    section: "Pipeline",
    type: "table",
    layout: [0, 0, 12, 1],
    contract: "Test",
    matrixRowIds: [],
    independentGates: [],
  },
  gate: { status: "Data", reasons: [], proofs: [] },
};
const measuredReport: PipelineReport = {
  ...pipelineReport,
  panels: {
    "p1-snapshot": {
      state: "Data",
      population: "completed scans",
      timeBasis: "completion time",
      rangeOperator: "start < timestamp <= end",
      metrics: [],
      columns: [],
      rows: [],
      series: [],
      reasons: [],
      provenance: {},
    },
  },
};

function preparedRegistry(registry: {
  routes: RouteDefinition[];
  widgets: Record<RouteId, WidgetResult[]>;
  measures: unknown[];
  controls: unknown[];
}) {
  return async () => ({
    resolve: () => registry,
  });
}

const app = createApp({
  prepareRegistry: preparedRegistry({
    routes: [route],
    widgets,
    measures: [],
    controls: [],
  }),
  probe: async () => ({
    state: "Data",
    message: "Local SigNoz read-only aggregate query succeeded.",
  }),
  pipeline: async () => pipelineReport,
  assets: new Map([
    [
      "/index.html",
      new Response("<!doctype html>", {
        headers: { "content-type": "text/html" },
      }),
    ],
  ]),
  now: () => new Date("2026-09-05T00:00:00.000Z"),
});

function request(path: string, headers: HeadersInit = {}): Request {
  return new Request(`http://127.0.0.1:4173${path}`, {
    headers: { host: "127.0.0.1:4173", ...headers },
  });
}

const validQuery =
  "route=pipeline&start=2026-09-01T00:00:00Z&end=2026-09-02T00:00:00Z&provider=&modelRole=Requested&model=&project=";
function pipelineFixture(source: string) {
  return {
    command: [process.execPath, "-e", source],
  };
}

describe("dashboard HTTP boundary", () => {
  test("serves only the SPA routes and does not expose API responses to caches", async () => {
    const response = await app(request("/pipeline"));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/html");
    expect(await response.text()).toBe("<!doctype html>");
    expect(response.headers.get("content-security-policy")).toContain(
      "default-src 'self'",
    );
    const health = await app(request("/api/health"));
    expect(health.headers.get("cache-control")).toBe("no-store");
    expect(health.headers.get("x-content-type-options")).toBe("nosniff");
  });

  test("rejects unknown, duplicate, malformed, and oversize query boundaries", async () => {
    expect(
      (await app(request(`/api/dashboard?${validQuery}&sql=SELECT`))).status,
    ).toBe(400);
    expect(
      (await app(request(`/api/dashboard?${validQuery}&project=again`))).status,
    ).toBe(400);
    expect(
      (
        await app(
          request(
            `/api/dashboard?route=pipeline&start=2026-02-30T00:00:00Z&end=2026-09-02T00:00:00Z&provider=&modelRole=Requested&model=&project=`,
          ),
        )
      ).status,
    ).toBe(400);
    expect(
      (
        await app(
          request(
            `/api/dashboard?route=pipeline&start=2026-07-01T00:00:00Z&end=2026-09-02T00:00:00Z&provider=&modelRole=Requested&model=&project=`,
          ),
        )
      ).status,
    ).toBe(400);
  });

  test("rejects DNS-rebinding host and cross-origin requests", async () => {
    expect(
      (
        await app(
          new Request(`http://127.0.0.1:4173/api/dashboard?${validQuery}`, {
            headers: { host: "attacker.example" },
          }),
        )
      ).status,
    ).toBe(403);
    expect(
      (
        await app(
          request(`/api/dashboard?${validQuery}`, {
            origin: "http://attacker.example",
          }),
        )
      ).status,
    ).toBe(403);
  });

  test("returns gated registry data without converting transport failures to empty data", async () => {
    const unavailable = createApp({
      prepareRegistry: preparedRegistry({
        routes: [route],
        widgets,
        measures: [],
        controls: [],
      }),
      probe: async () => ({
        state: "Query/system error",
        message: "Local SigNoz read-only aggregate query failed.",
      }),
      pipeline: async () => pipelineReport,
    });
    const response = await unavailable(request(`/api/dashboard?${validQuery}`));
    expect(response.status).toBe(200);
    expect((await response.json()).transport.state).toBe("Query/system error");
  });

  test("attaches validated pipeline measurements only to proven registry gates", async () => {
    const measurementApp = createApp({
      prepareRegistry: preparedRegistry({
        routes: [route],
        widgets: { ...widgets, pipeline: [measuredWidget] },
        measures: [],
        controls: [],
      }),
      pipeline: async () => measuredReport,
    });
    const body = await (
      await measurementApp(request(`/api/dashboard?${validQuery}`))
    ).json();
    const nonPipeline = validQuery.replace("route=pipeline", "route=provider");
    expect(
      (await measurementApp(request(`/api/dashboard?${nonPipeline}`))).status,
    ).toBe(503);
    expect(body.widgets[0].measurement.state).toBe("Data");
    const errorPanelApp = createApp({
      prepareRegistry: preparedRegistry({
        routes: [route],
        widgets: { ...widgets, pipeline: [measuredWidget] },
        measures: [],
        controls: [],
      }),
      pipeline: async () => ({
        ...measuredReport,
        panels: {
          "p1-snapshot": {
            ...measuredReport.panels["p1-snapshot"],
            state: "Query/system error",
          },
        },
      }),
    });
    const errorBody = await (
      await errorPanelApp(request(`/api/dashboard?${validQuery}`))
    ).json();
    expect(errorBody.widgets[0].measurement.state).toBe("Query/system error");

    const invalidApp = createApp({
      prepareRegistry: preparedRegistry({
        routes: [route],
        widgets: { ...widgets, pipeline: [measuredWidget] },
        measures: [],
        controls: [],
      }),
      pipeline: async () => ({ ...measuredReport, panels: {} }),
    });
    expect(
      (await invalidApp(request(`/api/dashboard?${validQuery}`))).status,
    ).toBe(503);
  });
  test("settles an already-cancelled request without starting registry or pipeline work", async () => {
    let prepared = false;
    let ranPipeline = false;
    const cancelledApp = createApp({
      prepareRegistry: async () => {
        prepared = true;
        return {
          resolve: () => ({
            routes: [route],
            widgets,
            measures: [],
            controls: [],
          }),
        };
      },
      pipeline: async () => {
        ranPipeline = true;
        return pipelineReport;
      },
    });
    const controller = new AbortController();
    controller.abort();
    const response = await cancelledApp(
      new Request(`http://127.0.0.1:4173/api/dashboard?${validQuery}`, {
        headers: { host: "127.0.0.1:4173" },
        signal: controller.signal,
      }),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: "pipeline measurement unavailable",
    });
    expect(prepared).toBe(false);
    expect(ranPipeline).toBe(false);
  });

  test("rejects a response aborted after registry, probe, or report completion", async () => {
    for (const stage of ["registry", "probe", "report"] as const) {
      const controller = new AbortController();
      const cancelledApp = createApp({
        prepareRegistry: async () => {
          if (stage === "registry") controller.abort();
          return {
            resolve: () => ({
              routes: [route],
              widgets,
              measures: [],
              controls: [],
            }),
          };
        },
        probe: async () => {
          if (stage === "probe") controller.abort();
          return {
            state: "Data",
            message: "Local SigNoz read-only aggregate query succeeded.",
          };
        },
        pipeline: async () => {
          if (stage === "report") controller.abort();
          return pipelineReport;
        },
      });
      const response = await cancelledApp(
        new Request(`http://127.0.0.1:4173/api/dashboard?${validQuery}`, {
          headers: { host: "127.0.0.1:4173" },
          signal: controller.signal,
        }),
      );
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({
        error: "pipeline measurement unavailable",
      });
    }
  });
  test("returns pipeline cancellation as a complete HTTP response before idle timeout", async () => {
    const deadlineApp = createApp({
      prepareRegistry: preparedRegistry({
        routes: [route],
        widgets,
        measures: [],
        controls: [],
      }),
      probe: async () => ({
        state: "Data",
        message: "Local SigNoz read-only aggregate query succeeded.",
      }),
      pipeline: async (_start, _end, signal) => {
        await new Promise<void>((resolve) =>
          signal.addEventListener("abort", () => resolve(), { once: true }),
        );
        throw new Error("pipeline request cancelled");
      },
    });
    const server = Bun.serve({
      hostname: "127.0.0.1",
      port: 0,
      idleTimeout: 15,
      fetch: deadlineApp,
    });
    try {
      const response = await fetch(
        new URL(`/api/dashboard?${validQuery}`, server.url),
        { headers: { host: server.url.host } },
      );
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({
        error: "pipeline measurement unavailable",
      });
    } finally {
      server.stop(true);
    }
  }, 25_000);

  test("settles malformed, oversized, and cancelled direct pipeline children", async () => {
    const malformed = pipelineFixture('process.stdout.write("{");');
    await expect(
      runPipeline(
        "2026-09-01T00:00:00Z",
        "2026-09-02T00:00:00Z",
        new AbortController().signal,
        undefined,
        malformed.command,
      ),
    ).rejects.toThrow();

    const oversized = pipelineFixture(
      'await Bun.write(Bun.stdout, "x".repeat(512 * 1024 + 1));',
    );
    await expect(
      runPipeline(
        "2026-09-01T00:00:00Z",
        "2026-09-02T00:00:00Z",
        new AbortController().signal,
        undefined,
        oversized.command,
      ),
    ).rejects.toThrow("response exceeds limit");

    const controller = new AbortController();
    const directory = await mkdtemp(join(tmpdir(), "pipeline-cancellation-"));
    const ready = join(directory, "ready");
    const terminated = join(directory, "terminated");
    const watcher = watch(directory);
    const readiness = new Promise<void>((resolve, reject) => {
      watcher.on("change", (_event, filename) => {
        if (filename === "ready") resolve();
      });
      watcher.once("error", reject);
    });
    // Real process-group signals require live descendant processes, not fake timers.
    const descendant = `process.on("SIGTERM", () => {}); require("node:fs").writeFileSync(${JSON.stringify(ready + ".pending")}, String(process.pid)); require("node:fs").renameSync(${JSON.stringify(ready + ".pending")}, ${JSON.stringify(ready)}); setInterval(() => {}, 1000);`;
    const stalled = pipelineFixture(
      `require("node:child_process").spawn(process.execPath, ["-e", ${JSON.stringify(descendant)}], { stdio: ["ignore", "inherit", "ignore"] }); process.on("SIGTERM", () => { require("node:fs").writeFileSync(${JSON.stringify(terminated)}, "terminated"); process.exit(0); }); setInterval(() => {}, 1000);`,
    );
    const stalledRun = runPipeline(
      "2026-09-01T00:00:00Z",
      "2026-09-02T00:00:00Z",
      controller.signal,
      undefined,
      stalled.command,
    );
    let descendantPid: number | undefined;
    const failures: unknown[] = [];
    try {
      await readiness;
      const readyPid = Number(await Bun.file(ready).text());
      if (!Number.isSafeInteger(readyPid) || readyPid <= 1)
        throw new Error("invalid owned descendant PID");
      descendantPid = readyPid;
      controller.abort();
      await expect(stalledRun).rejects.toThrow("pipeline query failed");
      expect(await Bun.file(terminated).exists()).toBe(true);
      let alive = true;
      try {
        process.kill(descendantPid, 0);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
        alive = false;
      }
      expect(alive).toBe(false);
    } catch (error) {
      failures.push(error);
    }
    controller.abort();
    await stalledRun.catch(() => undefined);
    watcher.close();
    if (descendantPid !== undefined) {
      try {
        process.kill(descendantPid, "SIGKILL");
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ESRCH")
          failures.push(error);
      }
    }
    try {
      await rm(directory, { recursive: true });
    } catch (error) {
      failures.push(error);
    }
    if (failures.length)
      throw new AggregateError(
        failures,
        "pipeline lifecycle verification failed",
      );
  });

  test("prepares independent registries for overlapping requests", async () => {
    let requestNumber = 0;
    const isolatedApp = createApp({
      prepareRegistry: async () => {
        const title = `Request ${++requestNumber}`;
        return {
          resolve: () => ({
            routes: [{ ...route, title }],
            widgets,
            measures: [],
            controls: [],
          }),
        };
      },
      probe: async () => ({
        state: "Data",
        message: "Local SigNoz read-only aggregate query succeeded.",
      }),
      pipeline: async () => pipelineReport,
    });
    const [first, second] = await Promise.all([
      isolatedApp(request(`/api/dashboard?${validQuery}`)),
      isolatedApp(request(`/api/dashboard?${validQuery}`)),
    ]);
    expect((await first.json()).route.title).toBe("Request 1");
    expect((await second.json()).route.title).toBe("Request 2");
  });

  test("exposes registry measures and controls through coverage", async () => {
    const coverageApp = createApp({
      prepareRegistry: preparedRegistry({
        routes: [route],
        widgets,
        measures: [{ id: "M1" }],
        controls: [{ id: "C1" }],
      }),
      probe: async () => ({
        state: "Data",
        message: "Local SigNoz read-only aggregate query succeeded.",
      }),
    });
    expect(await (await coverageApp(request("/api/coverage"))).json()).toEqual({
      measures: [{ id: "M1" }],
      controls: [{ id: "C1" }],
    });
  });

  test("accepts verified scalar aggregates including empty populations, but rejects malformed results", async () => {
    const aggregate = (count: unknown) => ({
      status: "success",
      data: {
        type: "scalar",
        data: {
          results: [
            {
              queryName: "A",
              columns: [{ name: "sample_count" }],
              data: [[count]],
            },
          ],
        },
      },
    });
    for (const count of [0, 1332]) {
      const queryApp = createApp({
        fetch: async () => Response.json(aggregate(count)),
      });
      expect(
        (await (await queryApp(request("/api/health"))).json()).state,
      ).toBe("Data");
    }
    for (const body of [
      aggregate(-1),
      aggregate(0.5),
      aggregate("3"),
      aggregate(null),
      { status: "success", data: { type: "scalar", data: { results: [] } } },
      { status: "error" },
    ]) {
      const queryApp = createApp({ fetch: async () => Response.json(body) });
      expect(
        (await (await queryApp(request("/api/health"))).json()).state,
      ).toBe("Query/system error");
    }
  });
});
