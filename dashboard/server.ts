import { spawn } from "node:child_process";
import { readdir } from "node:fs/promises";
import { extname, join, relative } from "node:path";
import { Readable } from "node:stream";
import { prepareRegistry } from "./src/registry";
import { isPipelineReport } from "./src/pipeline";
import type {
  DashboardResponse,
  FilterState,
  PipelineReport,
  RouteId,
  RouteDefinition,
  WidgetResult,
} from "./src/contracts";

const ADDRESS = "127.0.0.1";
const PORT = 4173;
const SIGNOZ_QUERY_URL = "http://127.0.0.1:8080/api/v5/query_range";
const QUERY_BODY_LIMIT_BYTES = 64 * 1024;
const PROBE_TIMEOUT_MS = 3_000;
// The measured 31-day report takes 12.635s before registry and probe work.
// Reserve 1.865s for those local steps while retaining a 500ms response envelope
// within Bun's 15s idle timeout.
const APPLICATION_TIMEOUT_MS = 14_500;
const HTTP_IDLE_TIMEOUT_SECONDS = 15;
const PIPELINE_OUTPUT_LIMIT_BYTES = 512 * 1024;
const PIPELINE_CLEANUP_HEADROOM_MS = 1_000;
const PROCESS_TERMINATION_GRACE_MS = 1_000;
const MAX_RANGE_MS = 31 * 24 * 60 * 60 * 1000;
const MAX_FILTER_LENGTH = 256;
const ROUTE_IDS: Record<RouteId, true> = {
  pipeline: true,
  provider: true,
  usage: true,
  tools: true,
  recurrence: true,
};
const ALLOWED_QUERY_KEYS: Record<string, true> = {
  route: true,
  start: true,
  end: true,
  provider: true,
  modelRole: true,
  model: true,
  project: true,
};
const CSP =
  "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; connect-src 'self'; img-src 'self'; script-src 'self'; style-src 'self';";
const MEDIA_TYPES: Record<string, string> = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
};

type Registry = {
  routes: RouteDefinition[];
  widgets: Record<RouteId, WidgetResult[]>;
  measures: unknown[];
  controls: unknown[];
};
type Probe = { state: "Data" | "Query/system error"; message: string };
type Fetch = (
  input: Parameters<typeof fetch>[0],
  init?: Parameters<typeof fetch>[1],
) => Promise<Response>;
type PipelineRunner = (
  start: string,
  end: string,
  signal: AbortSignal,
  deadlineUnixMs?: number,
) => Promise<unknown>;
type PreparedRegistry = {
  resolve: (implementation?: PipelineReport["implementation"]) => Registry;
};
type ServerDependencies = {
  prepareRegistry: (signal?: AbortSignal) => Promise<PreparedRegistry>;
  coverage: (signal: AbortSignal) => Promise<unknown>;
  probe: (signal: AbortSignal) => Promise<Probe>;
  pipeline: PipelineRunner;
  assets: Map<string, Response>;
  now: () => Date;
  fetch: Fetch;
};

const apiHeaders = {
  "cache-control": "no-store",
  "content-type": "application/json; charset=utf-8",
  "x-content-type-options": "nosniff",
};
const securityHeaders = {
  "content-security-policy": CSP,
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: { ...securityHeaders, ...apiHeaders },
  });
}

function requestError(message: string): Response {
  return json({ error: message }, 400);
}

function isTrustedRequest(request: Request): boolean {
  const url = new URL(request.url);
  const expectedHost = `${ADDRESS}:${url.port || PORT}`;
  if (url.hostname !== ADDRESS || request.headers.get("host") !== expectedHost)
    return false;
  const origin = request.headers.get("origin");
  return origin === null || origin === `http://${expectedHost}`;
}

function parseUtc(value: string | null, name: string): Date | Response {
  if (
    value === null ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value)
  ) {
    return requestError(`${name} must be an RFC 3339 UTC timestamp`);
  }
  const parsed = new Date(value);
  const milliseconds = value.includes(".")
    ? value.slice(value.indexOf(".") + 1, -1).padEnd(3, "0")
    : "000";
  const normalized = `${value.slice(0, 19)}.${milliseconds}Z`;
  return Number.isNaN(parsed.getTime()) || parsed.toISOString() !== normalized
    ? requestError(`${name} must be a valid UTC timestamp`)
    : parsed;
}

function parseDashboardRequest(
  url: URL,
): { route: RouteId; filters: FilterState } | Response {
  for (const [key] of url.searchParams) {
    if (ALLOWED_QUERY_KEYS[key] !== true)
      return requestError(`unknown query parameter: ${key}`);
    if (url.searchParams.getAll(key).length !== 1)
      return requestError(`duplicate query parameter: ${key}`);
  }
  const route = url.searchParams.get("route");
  if (route === null || ROUTE_IDS[route as RouteId] !== true)
    return requestError("route must be a known dashboard route");
  const start = parseUtc(url.searchParams.get("start"), "start");
  const end = parseUtc(url.searchParams.get("end"), "end");
  if (start instanceof Response) return start;
  if (end instanceof Response) return end;
  if (start >= end) return requestError("start must be before end");
  if (end.getTime() - start.getTime() > MAX_RANGE_MS)
    return requestError("selected range exceeds the 31-day limit");

  const modelRole = url.searchParams.get("modelRole");
  if (modelRole !== "Requested" && modelRole !== "Response")
    return requestError("modelRole must be Requested or Response");
  const strings = ["provider", "model", "project"] as const;
  const values = Object.fromEntries(
    strings.map((key) => [key, url.searchParams.get(key)]),
  );
  for (const key of strings) {
    const value = values[key];
    if (value === null || value.length > MAX_FILTER_LENGTH)
      return requestError(
        `${key} must be at most ${MAX_FILTER_LENGTH} characters`,
      );
  }
  return {
    route: route as RouteId,
    filters: {
      start: url.searchParams.get("start")!,
      end: url.searchParams.get("end")!,
      provider: values.provider!,
      modelRole,
      model: values.model!,
      project: values.project!,
    },
  };
}

function cancellationBudget(signal: AbortSignal): {
  signal: AbortSignal;
  deadlineUnixMs: number;
  dispose: () => void;
} {
  const controller = new AbortController();
  const deadlineUnixMs = Date.now() + APPLICATION_TIMEOUT_MS;
  const timeout = setTimeout(
    () => controller.abort(),
    Math.max(0, deadlineUnixMs - Date.now()),
  );
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  return {
    signal: AbortSignal.any([signal, controller.signal]),
    deadlineUnixMs,
    dispose: () => {
      clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
    },
  };
}

async function readBoundedJson(response: Response): Promise<unknown> {
  if (!response.body) throw new Error("missing response body");
  return JSON.parse(
    await readBoundedText(response.body, QUERY_BODY_LIMIT_BYTES),
  );
}

async function readBoundedText(
  stream: ReadableStream<Uint8Array>,
  limit = PIPELINE_OUTPUT_LIMIT_BYTES,
  signal?: AbortSignal,
): Promise<string> {
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let cancellation: Promise<void> | undefined;
  let cancellationError: unknown;
  let cancellationFailed = false;
  let failed = false;
  let failure: unknown;
  let cleanupFailed = false;
  let cleanupError: unknown;
  const cancel = (): Promise<void> =>
    (cancellation ??= reader.cancel().catch((error: unknown) => {
      cancellationError = error;
      cancellationFailed = true;
    }));
  const onAbort = () => {
    cancel();
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new Error("response exceeds limit");
      chunks.push(value);
    }
  } catch (error) {
    failed = true;
    failure = error;
  }
  signal?.removeEventListener("abort", onAbort);
  try {
    await cancel();
  } catch (error) {
    cleanupFailed = true;
    cleanupError = error;
  }
  if (cancellationFailed) {
    cleanupError = cleanupFailed
      ? new AggregateError(
          [cleanupError, cancellationError],
          "response cleanup failed",
        )
      : cancellationError;
    cleanupFailed = true;
  }
  try {
    reader.releaseLock();
  } catch (error) {
    cleanupError = cleanupFailed
      ? new AggregateError([cleanupError, error], "response cleanup failed")
      : error;
    cleanupFailed = true;
  }
  if (failed && cleanupFailed)
    throw new AggregateError([cleanupError], "response cleanup failed", {
      cause: failure,
    });
  if (failed) throw failure;
  if (cleanupFailed) throw cleanupError;
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}
export async function runPipeline(
  start: string,
  end: string,
  signal: AbortSignal,
  deadlineUnixMs = Date.now() + APPLICATION_TIMEOUT_MS,
  command = [
    join(import.meta.dir, "..", ".venv", "bin", "python"),
    "-m",
    "agent_introspection.pipeline_dashboard",
    "--start",
    start,
    "--end",
    end,
  ],
): Promise<unknown> {
  const childDeadlineUnixMs = deadlineUnixMs - PIPELINE_CLEANUP_HEADROOM_MS;
  if (signal.aborted || childDeadlineUnixMs <= Date.now())
    throw new Error("pipeline query failed");
  const child = spawn(command[0]!, command.slice(1), {
    cwd: join(import.meta.dir, ".."),
    detached: true,
    env: {
      ...process.env,
      PIPELINE_REQUEST_DEADLINE_UNIX_MS: String(childDeadlineUnixMs),
    },
    stdio: ["ignore", "pipe", "ignore"],
  });
  if (!child.stdout) throw new Error("pipeline query failed");

  let exited = false;
  const childExited = new Promise<number>((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => {
      exited = true;
      resolve(code ?? -1);
    });
  });
  const reap = (): Promise<void> =>
    new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(new Error("pipeline cleanup timed out")),
        Math.max(0, Math.min(100, deadlineUnixMs + 100 - Date.now())),
      );
      childExited.then(
        () => {
          clearTimeout(timeout);
          resolve();
        },
        (error) => {
          clearTimeout(timeout);
          reject(error);
        },
      );
    });
  let termination: Promise<void> | undefined;
  const signalGroup = async (
    name: NodeJS.Signals,
    reaped = false,
  ): Promise<boolean> => {
    if (child.pid === undefined) return false;
    try {
      process.kill(-child.pid, name);
      return true;
    } catch (error) {
      if (
        error &&
        typeof error === "object" &&
        "code" in error &&
        error.code === "ESRCH"
      )
        return false;
      if (
        !reaped &&
        error &&
        typeof error === "object" &&
        "code" in error &&
        error.code === "EPERM"
      ) {
        // Darwin can report EPERM for an all-zombie group before child reaping.
        // Recheck only after observing exit; persistent permission errors still fail.
        await reap();
        return signalGroup(name, true);
      }
      throw error;
    }
  };
  const reapGroup = async (): Promise<void> => {
    const deadline = Math.min(Date.now() + 100, deadlineUnixMs + 100);
    while (child.pid !== undefined) {
      try {
        process.kill(-child.pid, 0);
      } catch (error) {
        const code = (error as NodeJS.ErrnoException).code;
        if (code === "ESRCH") return;
        if (code !== "EPERM") throw error;
      }
      if (Date.now() >= deadline)
        throw new Error("pipeline process group cleanup timed out");
      await new Promise<void>((resolve) => setTimeout(resolve, 1));
    }
  };
  const terminate = (): Promise<void> =>
    (termination ??= (async () => {
      if (await signalGroup("SIGTERM")) {
        await new Promise<void>((resolve) => {
          setTimeout(
            resolve,
            Math.min(
              PROCESS_TERMINATION_GRACE_MS,
              Math.max(0, deadlineUnixMs - Date.now()),
            ),
          );
        });
        await signalGroup("SIGKILL");
      }
      await reap();
      await reapGroup();
    })());
  const onAbort = () => {
    void terminate().catch(() => undefined);
  };
  signal.addEventListener("abort", onAbort, { once: true });
  let failed = false;
  let failure: unknown;
  let cleanupFailed = false;
  let cleanupError: unknown;
  let result: unknown;
  try {
    const stdout = readBoundedText(
      Readable.toWeb(child.stdout) as unknown as ReadableStream<Uint8Array>,
      PIPELINE_OUTPUT_LIMIT_BYTES,
      signal,
    ).catch((error: unknown) => {
      void terminate().catch(() => undefined);
      throw error;
    });
    const [output, exitCode] = await Promise.all([stdout, childExited]);
    if (signal.aborted || exitCode !== 0)
      throw new Error("pipeline query failed");
    result = JSON.parse(output);
  } catch (error) {
    failed = true;
    failure = error;
  }
  signal.removeEventListener("abort", onAbort);
  try {
    if (failed || !exited) await terminate();
  } catch (error) {
    cleanupFailed = true;
    cleanupError = error;
  }
  if (failed && cleanupFailed)
    throw new AggregateError([cleanupError], "pipeline query failed", {
      cause: failure,
    });
  if (failed) throw failure;
  if (cleanupFailed) throw cleanupError;
  return result;
}

function isAggregateQueryResponse(body: unknown): boolean {
  if (
    !body ||
    typeof body !== "object" ||
    !("status" in body) ||
    body.status !== "success" ||
    !("data" in body)
  )
    return false;
  const envelope = body.data;
  if (
    !envelope ||
    typeof envelope !== "object" ||
    !("type" in envelope) ||
    envelope.type !== "scalar" ||
    !("data" in envelope)
  )
    return false;
  const data = envelope.data;
  if (
    !data ||
    typeof data !== "object" ||
    !("results" in data) ||
    !Array.isArray(data.results) ||
    data.results.length !== 1
  )
    return false;
  const result = data.results[0];
  if (
    !result ||
    result.queryName !== "A" ||
    !Array.isArray(result.columns) ||
    result.columns.length !== 1 ||
    result.columns[0]?.name !== "sample_count" ||
    !Array.isArray(result.data) ||
    result.data.length !== 1 ||
    !Array.isArray(result.data[0]) ||
    result.data[0].length !== 1
  )
    return false;
  const count: unknown = result.data[0][0];
  return typeof count === "number" && Number.isSafeInteger(count) && count >= 0;
}

function signozProbe(fetchFn: Fetch, signal: AbortSignal): Promise<Probe> {
  const query =
    "SELECT count() AS sample_count FROM signoz_logs.distributed_logs_v2 WHERE timestamp >= {{.start_timestamp_nano}} AND timestamp <= {{.end_timestamp_nano}} AND resources_string['service.name'] = 'agent-introspection'";
  const end = Date.now();
  const body = JSON.stringify({
    start: end - 3_600_000,
    end,
    requestType: "scalar",
    compositeQuery: {
      queries: [{ type: "clickhouse_sql", spec: { name: "A", query } }],
    },
  });
  return (async () => {
    try {
      const response = await fetchFn(SIGNOZ_QUERY_URL, {
        method: "POST",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
        },
        body,
        signal: AbortSignal.any([
          signal,
          AbortSignal.timeout(PROBE_TIMEOUT_MS),
        ]),
      });
      if (
        response.status !== 200 ||
        !isAggregateQueryResponse(await readBoundedJson(response))
      )
        throw new Error("invalid query response");
      return {
        state: "Data",
        message: "Local SigNoz read-only aggregate query succeeded.",
      };
    } catch {
      return {
        state: "Query/system error",
        message: "Local SigNoz read-only aggregate query failed.",
      };
    }
  })();
}

async function loadAssets(): Promise<Map<string, Response>> {
  const root = join(import.meta.dir, "dist");
  const assets = new Map<string, Response>();
  async function add(directory: string): Promise<void> {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = join(directory, entry.name);
      if (entry.isDirectory()) await add(file);
      else if (entry.isFile()) {
        const path = `/${relative(root, file).replaceAll("\\", "/")}`;
        assets.set(
          path,
          new Response(await Bun.file(file).arrayBuffer(), {
            headers: {
              ...securityHeaders,
              "content-type":
                MEDIA_TYPES[extname(path)] ?? "application/octet-stream",
            },
          }),
        );
      }
    }
  }
  try {
    await add(root);
  } catch {
    // Build output is intentionally required before the server can serve the SPA.
  }
  return assets;
}

export function createApp(
  overrides: Partial<ServerDependencies> = {},
): (request: Request) => Promise<Response> {
  const dependencies: ServerDependencies = {
    prepareRegistry,
    coverage: async (signal) => {
      const { resolve } = await dependencies.prepareRegistry(signal);
      const { measures, controls } = resolve();
      return { measures, controls };
    },
    probe: (signal) => signozProbe(overrides.fetch ?? fetch, signal),
    pipeline: runPipeline,
    assets: new Map(),
    now: () => new Date(),
    fetch,
    ...overrides,
  };
  return async (request) => {
    if (!isTrustedRequest(request))
      return json({ error: "untrusted host or origin" }, 403);
    if (request.method !== "GET" && request.method !== "HEAD")
      return json({ error: "method not allowed" }, 405);
    if (
      request.headers.get("content-length") !== null &&
      request.headers.get("content-length") !== "0"
    )
      return requestError("request bodies are not accepted");
    const url = new URL(request.url);
    if (url.pathname === "/api/health") {
      const budget = cancellationBudget(request.signal);
      try {
        if (budget.signal.aborted)
          return json({
            state: "Query/system error",
            message: "Local SigNoz read-only aggregate query failed.",
          });
        const probe = await dependencies.probe(budget.signal);
        if (budget.signal.aborted) throw new Error("request cancelled");
        return json(probe);
      } catch {
        return json({
          state: "Query/system error",
          message: "Local SigNoz read-only aggregate query failed.",
        });
      } finally {
        budget.dispose();
      }
    }
    if (url.pathname === "/api/coverage") {
      const budget = cancellationBudget(request.signal);
      try {
        if (budget.signal.aborted)
          return json({ error: "dashboard registry is unavailable" }, 503);
        const coverage = await dependencies.coverage(budget.signal);
        if (budget.signal.aborted) throw new Error("request cancelled");
        return json(coverage);
      } catch {
        return json({ error: "dashboard registry is unavailable" }, 503);
      } finally {
        budget.dispose();
      }
    }
    if (url.pathname === "/api/dashboard") {
      const parsed = parseDashboardRequest(url);
      if (parsed instanceof Response) return parsed;
      const budget = cancellationBudget(request.signal);
      try {
        if (budget.signal.aborted)
          return json({ error: "pipeline measurement unavailable" }, 503);
        const prepared = await dependencies.prepareRegistry(budget.signal);
        if (budget.signal.aborted) throw new Error("request cancelled");
        let registry = prepared.resolve();
        const transport = await dependencies.probe(budget.signal);
        if (budget.signal.aborted) throw new Error("request cancelled");
        const route = registry.routes.find(
          (candidate) => candidate.id === parsed.route,
        );
        if (!route)
          return json({ error: "dashboard registry is unavailable" }, 503);
        let widgets = registry.widgets[parsed.route];
        if (parsed.route === "pipeline") {
          const report = await dependencies.pipeline(
            parsed.filters.start,
            parsed.filters.end,
            budget.signal,
            budget.deadlineUnixMs,
          );
          if (budget.signal.aborted) throw new Error("request cancelled");
          if (
            !isPipelineReport(report) ||
            report.start !== parsed.filters.start ||
            report.end !== parsed.filters.end
          )
            return json({ error: "pipeline measurement unavailable" }, 503);
          registry = prepared.resolve(report.implementation);
          const qualifiedRoute = registry.routes.find(
            (candidate) => candidate.id === parsed.route,
          );
          if (!qualifiedRoute)
            return json({ error: "dashboard registry is unavailable" }, 503);
          widgets = registry.widgets.pipeline;
          const panelIds = widgets.map((result) => result.widget.id);
          if (!isPipelineReport(report, panelIds))
            return json({ error: "pipeline measurement unavailable" }, 503);
          widgets = widgets.map((result) =>
            result.gate.status === "Data"
              ? { ...result, measurement: report.panels[result.widget.id] }
              : result,
          );
        }
        const response: DashboardResponse = {
          route,
          filters: parsed.filters,
          widgets,
          queriedAt: dependencies.now().toISOString(),
          transport,
        };
        return json(response);
      } catch {
        return json({ error: "pipeline measurement unavailable" }, 503);
      } finally {
        budget.dispose();
      }
    }
    const assetPath =
      ROUTE_IDS[url.pathname.slice(1) as RouteId] === true
        ? "/index.html"
        : url.pathname;
    const asset = dependencies.assets.get(assetPath);
    if (!asset) return json({ error: "not found" }, 404);
    const headers = new Headers(asset.headers);
    for (const [name, value] of Object.entries(securityHeaders))
      headers.set(name, value);
    return new Response(
      request.method === "HEAD" ? null : await asset.clone().arrayBuffer(),
      { headers },
    );
  };
}

if (import.meta.main) {
  const assets = await loadAssets();
  Bun.serve({
    hostname: ADDRESS,
    port: PORT,
    idleTimeout: HTTP_IDLE_TIMEOUT_SECONDS,
    fetch: createApp({ assets }),
  });
  console.log(`Dashboard listening on http://${ADDRESS}:${PORT}`);
}
