import {
  HARNESSES,
  DETAIL_PATHS,
  PAGE_PATHS,
  VIEW_IDS,
  type Features,
  type Filters,
  type Harness,
  type Registry,
  type Row,
  type SessionResponse,
  type ViewId,
  type ViewResponse,
} from "./src/contracts";
import {
  batch,
  batchSettled,
  clickhouse,
  clickhouseUrl,
  credentialsFromEnv,
  live,
  shortMessage,
  SNAPSHOT_DAYS,
  type Query,
} from "./server/clickhouse";
import { asOfFor, REFRESHERS, refreshersFor } from "./server/refreshers";
import { experimentalFeatures, gatedSignals } from "./server/features";
import { enabledHarnesses, hiddenSignals } from "./src/harness-scope";
import {
  contributions,
  pipelineData,
  routeCounts,
  sanitizationRows,
  unroutedRows,
  type RouteCounts,
  type Unrouted,
} from "./server/pipeline";
import { interventionsData } from "./server/interventions";
import { MIN_RATE_N, SESSION_QUERIES, VIEW_QUERIES } from "./server/views";
import { sqliteWorkflow, type WorkflowReader } from "./server/workflow";
import { loadAssets } from "./server/assets";

const ADDRESS = "127.0.0.1";
const PORT = 4173;
const MAX_RANGE_MS = 92 * 86_400_000;
const REGISTRY_TTL_MS = 60_000;
const SHARED_TTL_MS = 600_000;
const CACHE_ENTRIES = 32;
const UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
const CSP =
  "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; connect-src 'self'; img-src 'self'; script-src 'self'; style-src 'self';";
const securityHeaders = {
  "content-security-policy": CSP,
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

type Dependencies = {
  query: Query;
  workflow: WorkflowReader;
  assets: Map<string, Response>;
  now: () => Date;
  features: Features;
};

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      ...securityHeaders,
      "cache-control": "no-store",
      "content-type": "application/json; charset=utf-8",
    },
  });
}

const badRequest = (message: string) => json({ error: message }, 400);

function isTrustedRequest(request: Request): boolean {
  const url = new URL(request.url);
  const expectedHost = `${ADDRESS}:${url.port || PORT}`;
  if (url.hostname !== ADDRESS || request.headers.get("host") !== expectedHost)
    return false;
  const origin = request.headers.get("origin");
  return origin === null || origin === `http://${expectedHost}`;
}

const isHarness = (value: string): value is Harness =>
  (HARNESSES as readonly string[]).includes(value);

export function parseFilters(url: URL): Filters | Response {
  const start = url.searchParams.get("start") ?? "";
  const end = url.searchParams.get("end") ?? "";
  const harness = url.searchParams.get("harness") ?? "";
  if (!UTC.test(start) || !UTC.test(end))
    return badRequest("start and end must be RFC 3339 UTC timestamps");
  const span = Date.parse(end) - Date.parse(start);
  if (!(span > 0)) return badRequest("start must be before end");
  if (span > MAX_RANGE_MS)
    return badRequest("the selected range exceeds 92 days");
  if (harness !== "" && !isHarness(harness))
    return badRequest("harness must be empty (All) or a known harness");
  return { start, end, harness };
}

const params = (filters: Filters): Record<string, string> => ({
  start: filters.start.replace("T", " ").replace("Z", ""),
  end: filters.end.replace("T", " ").replace("Z", ""),
  harness: filters.harness,
});

export function ttlCache<T>(
  ttlMs: number,
  maxEntries: number,
  now: () => number,
): (key: string, load: () => Promise<T>) => Promise<T> {
  const entries = new Map<string, { at: number; value: Promise<T> }>();
  return (key, load) => {
    const at = now();
    for (const [entry, { at: stored }] of entries)
      if (at - stored > ttlMs) entries.delete(entry);
    const hit = entries.get(key);
    if (hit) return hit.value;
    const value = load();
    entries.set(key, { at, value });
    value.catch(() => {
      if (entries.get(key)?.value === value) entries.delete(key);
    });
    if (entries.size > maxEntries) entries.delete(entries.keys().next().value!);
    return value;
  };
}

async function loadRegistry(
  query: Query,
  features: Features,
): Promise<Registry> {
  const { signals, support, routes, strays, exclusions, harnesses } =
    await batch(query, {
      signals:
        "SELECT signal, view, view_title, title, question, unit, formula, scope FROM introspection.signals ORDER BY sort",
      support:
        "SELECT signal, harness, harness_label, route, alignment, note FROM introspection.signal_support ORDER BY signal, harness",
      routes:
        "SELECT route, harness, harness_label, source, match, expect, description FROM introspection.signal_routes ORDER BY route, harness",
      strays:
        "SELECT stray, harness, source, match, reason FROM introspection.signal_strays ORDER BY stray",
      exclusions:
        "SELECT signal, view, title, missing, reason FROM introspection.signal_exclusions ORDER BY signal",
      harnesses:
        "SELECT harness, label, enabled FROM introspection.harnesses ORDER BY harness",
    });
  if (!signals || signals.length === 0)
    throw new Error("signal support registry is empty; run facts install");
  const gated = gatedSignals(features);
  const registry = {
    signals: signals.filter((row) => !gated.has(String(row["signal"]))),
    support: support?.filter((row) => !gated.has(String(row["signal"]))),
    routes,
    strays,
    exclusions,
    harnesses,
  } as unknown as Omit<Registry, "hidden" | "features">;
  if (enabledHarnesses(registry).length === 0)
    throw new Error("no harness is enabled; run facts install");
  return { ...registry, hidden: hiddenSignals(registry), features };
}

const tenMinutes = (bound: Record<string, string>) =>
  `${bound["start"]!.slice(0, 15)}|${bound["end"]!.slice(0, 15)}`;

export function createApp(
  overrides: Partial<Dependencies> = {},
): (request: Request) => Promise<Response> {
  const dependencies: Dependencies = {
    query:
      overrides.query ??
      clickhouse(clickhouseUrl(), fetch, credentialsFromEnv()),
    workflow: sqliteWorkflow(),
    assets: new Map(),
    now: () => new Date(),
    features: overrides.features ?? experimentalFeatures(),
    ...overrides,
  };
  const { query, features } = dependencies;
  let cached: { at: number; registry: Promise<Registry> } | null = null;
  const registry = (): Promise<Registry> => {
    const now = dependencies.now().getTime();
    if (!cached || now - cached.at > REGISTRY_TTL_MS) {
      const pending = loadRegistry(query, features);
      cached = { at: now, registry: pending };
      pending.catch(() => {
        if (cached?.registry === pending) cached = null;
      });
    }
    return cached.registry;
  };

  const clock = () => dependencies.now().getTime();
  const routeCountCache = ttlCache<RouteCounts>(
    SHARED_TTL_MS,
    CACHE_ENTRIES,
    clock,
  );
  const checkCache = ttlCache<Row[]>(SHARED_TTL_MS, CACHE_ENTRIES, clock);
  const unroutedCache = ttlCache<Unrouted>(SHARED_TTL_MS, CACHE_ENTRIES, clock);

  const refreshersCache = ttlCache<Row[]>(SHARED_TTL_MS, 1, clock);
  const factsAsOf = (scope: string[] | null): Promise<ViewResponse["asOf"]> =>
    refreshersCache("facts", () => query(REFRESHERS))
      .then((rows) => asOfFor(rows, scope))
      .catch(() => null);

  async function view(id: ViewId, filters: Filters): Promise<ViewResponse> {
    const started = performance.now();
    const bound = params(filters);
    const reg = await registry();
    const registryDone = performance.now();
    const countsMs = { value: 0 };
    const counts = routeCountCache(tenMinutes(bound), () =>
      routeCounts(query, reg, bound),
    );
    counts.then(
      () => {
        countsMs.value = performance.now() - registryDone;
      },
      () => undefined,
    );
    const withinSnapshots =
      Date.parse(filters.start) >=
      dependencies.now().getTime() - (SNAPSHOT_DAYS + 1) * 86_400_000;
    let data: Record<string, Row[]>;
    let errors: Record<string, string>;
    let asOf: Promise<ViewResponse["asOf"]>;
    if (id === "pipeline") {
      asOf = factsAsOf(null);
      ({ data, errors } = await pipelineData(
        query,
        reg,
        bound,
        withinSnapshots,
        {
          counts,
          sanitization: checkCache("sanitization", () =>
            sanitizationRows(query),
          ),
          unrouted: unroutedCache(`unrouted|${tenMinutes(bound)}`, () =>
            unroutedRows(query, reg, bound),
          ),
        },
      ));
    } else {
      const queries =
        id === "interventions" && !features.intervention_outcomes
          ? {}
          : VIEW_QUERIES[id];
      asOf = factsAsOf(refreshersFor(queries, withinSnapshots));
      ({ data, errors } = await batchSettled(
        query,
        withinSnapshots ? queries : live(queries),
        bound,
      ));
      const names = Object.keys(queries);
      if (names.length > 0 && Object.keys(errors).length === names.length)
        throw new Error(Object.values(errors)[0]);
      if (id === "interventions")
        data = interventionsData(
          data,
          dependencies.workflow(),
          filters,
          features.intervention_outcomes,
        );
    }
    const sqlDone = performance.now();
    const totals = await counts.catch((error: unknown) => {
      errors["route_counts"] = shortMessage(error);
      return null;
    });
    const contributed = totals ? contributions(reg, totals, id) : {};
    return {
      view: id,
      filters,
      queriedAt: dependencies.now().toISOString(),
      elapsedMs: Math.round(performance.now() - started),
      timings: {
        registryMs: Math.round(registryDone - started),
        routeCountsMs: Math.round(countsMs.value),
        sqlMs: Math.round(sqlDone - registryDone),
      },
      asOf: await asOf,
      data,
      errors,
      thresholds: { minRateN: MIN_RATE_N },
      contributions: contributed,
    };
  }

  async function viewResponse(url: URL): Promise<Response> {
    const id = url.searchParams.get("view") ?? "";
    if (!(VIEW_IDS as readonly string[]).includes(id))
      return badRequest("view must be a known view");
    const filters = parseFilters(url);
    if (filters instanceof Response) return filters;
    if (
      filters.harness !== "" &&
      !enabledHarnesses(await registry()).includes(filters.harness)
    )
      return badRequest("harness is not enabled on this machine");
    return json(await view(id as ViewId, filters));
  }

  async function sessionResponse(url: URL): Promise<Response> {
    const harness = url.searchParams.get("harness") ?? "";
    const session = url.searchParams.get("session") ?? "";
    if (!isHarness(harness) || session === "" || session.length > 256)
      return badRequest("harness and session are required");
    if (!enabledHarnesses(await registry()).includes(harness))
      return badRequest("harness is not enabled on this machine");
    const response: SessionResponse = {
      harness,
      session,
      queriedAt: dependencies.now().toISOString(),
      data: await batch(query, live(SESSION_QUERIES), { harness, session }),
    };
    return json(response);
  }

  async function apiResponse(url: URL): Promise<Response | undefined> {
    try {
      if (url.pathname === "/api/registry") return json(await registry());
      if (url.pathname === "/api/view") return await viewResponse(url);
      if (url.pathname === "/api/session") return await sessionResponse(url);
    } catch (error) {
      return json(
        { error: error instanceof Error ? error.message : "query failed" },
        503,
      );
    }
    return undefined;
  }

  async function assetResponse(request: Request, url: URL): Promise<Response> {
    if (url.pathname.startsWith("/api/"))
      return json({ error: "not found" }, 404);
    const route = url.pathname.slice(1);
    const assetPath =
      route === "" ||
      route === "session" ||
      (VIEW_IDS as readonly string[]).includes(route) ||
      (DETAIL_PATHS as readonly string[]).includes(route) ||
      (PAGE_PATHS as readonly string[]).includes(route)
        ? "/index.html"
        : url.pathname;
    const asset = dependencies.assets.get(assetPath);
    if (!asset) return json({ error: "not found" }, 404);
    return new Response(
      request.method === "HEAD" ? null : await asset.clone().arrayBuffer(),
      { headers: new Headers(asset.headers) },
    );
  }

  return async (request) => {
    if (!isTrustedRequest(request))
      return json({ error: "untrusted host or origin" }, 403);
    if (request.method !== "GET" && request.method !== "HEAD")
      return json({ error: "method not allowed" }, 405);
    const url = new URL(request.url);
    return (await apiResponse(url)) ?? assetResponse(request, url);
  };
}

if (import.meta.main) {
  const assets = await loadAssets(securityHeaders);
  const app = createApp({ assets });
  Bun.serve({ hostname: ADDRESS, port: PORT, fetch: app });
  void app(
    new Request(`http://${ADDRESS}:${PORT}/api/registry`, {
      headers: { host: `${ADDRESS}:${PORT}` },
    }),
  );
  console.log(`Dashboard listening on http://${ADDRESS}:${PORT}`);
}
