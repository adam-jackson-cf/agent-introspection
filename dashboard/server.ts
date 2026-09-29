import { Database } from "bun:sqlite";
import { readdir } from "node:fs/promises";
import { readdirSync } from "node:fs";
import { homedir } from "node:os";
import { extname, join, relative } from "node:path";
import {
  HARNESSES,
  VIEW_IDS,
  type Filters,
  type Harness,
  type Registry,
  type Row,
  type SessionResponse,
  type ViewId,
  type ViewResponse,
} from "./src/contracts";
import { batch, clickhouse, type Query } from "./server/clickhouse";
import { contributions, pipelineData, routeCounts } from "./server/pipeline";
import { SESSION_QUERIES, VIEW_QUERIES } from "./server/views";

const ADDRESS = "127.0.0.1";
const PORT = 4173;
const CLICKHOUSE_URL =
  process.env.INTROSPECTION_CLICKHOUSE_URL ??
  "http://signoz-clickhouse.orb.local:8123";
const WORKFLOW_DB =
  process.env.INTROSPECTION_WORKFLOW_DB ??
  join(homedir(), ".local/share/agent-introspection/introspection.sqlite3");
/** Hook files waiting for `facts sync-projects` (a backlog means the job is not running). */
const PROJECT_INBOX =
  process.env.INTROSPECTION_PROJECT_INBOX ??
  join(homedir(), ".local/share/agent-introspection/session-context-inbox");
const MAX_RANGE_MS = 92 * 86_400_000;
const REGISTRY_TTL_MS = 60_000;
/** Route counts feed coverage and contributor chips; loaders add rows once a minute. */
const ROUTE_COUNT_TTL_MS = 60_000;
const ROUTE_COUNT_ENTRIES = 32;
const UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
const CSP =
  "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; connect-src 'self'; img-src 'self'; script-src 'self'; style-src 'self';";
const MEDIA_TYPES: Record<string, string> = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
};
const securityHeaders = {
  "content-security-policy": CSP,
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

/** Reads the workflow store's findings and proposals for the Interventions view. */
export type WorkflowReader = () => Record<string, Row[]>;
/** Counts hook files not yet synced into ClickHouse. */
export type InboxReader = () => number;

const countInbox: InboxReader = () => {
  try {
    return readdirSync(PROJECT_INBOX).filter((name) => name.endsWith(".json"))
      .length;
  } catch (error) {
    // The hooks create the inbox on their first write; until then nothing waits.
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return 0;
    throw error;
  }
};

type Dependencies = {
  query: Query;
  workflow: WorkflowReader;
  inbox: InboxReader;
  assets: Map<string, Response>;
  now: () => Date;
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

async function loadRegistry(query: Query): Promise<Registry> {
  const { signals, support, routes, strays } = await batch(query, {
    signals:
      "SELECT signal, view, view_title, title, question, unit, formula, scope FROM introspection.signals ORDER BY sort",
    support:
      "SELECT signal, harness, harness_label, route, alignment, note FROM introspection.signal_support ORDER BY signal, harness",
    routes:
      "SELECT route, harness, harness_label, source, match, expect, description FROM introspection.signal_routes ORDER BY route, harness",
    strays:
      "SELECT stray, harness, source, match, reason FROM introspection.signal_strays ORDER BY stray",
  });
  if (!signals || signals.length === 0)
    throw new Error("signal support registry is empty; run facts install");
  return { signals, support, routes, strays } as unknown as Registry;
}

/** Opens the SQLite workflow store read-only for each request. */
export function sqliteWorkflow(path = WORKFLOW_DB): WorkflowReader {
  return () => {
    const db = new Database(path, { readonly: true });
    try {
      const all = (sql: string) => db.query(sql).all() as Row[];
      return {
        findings:
          all(`SELECT category, detector_id AS detector, trend_state AS state,
            occurrence_count AS occurrences, canonical_task_count AS tasks, local_day_count AS days,
            strftime('%Y-%m-%dT%H:%M:%SZ', first_seen_ns / 1e9, 'unixepoch') AS first_seen,
            strftime('%Y-%m-%dT%H:%M:%SZ', last_seen_ns / 1e9, 'unixepoch') AS last_seen
          FROM findings WHERE is_active = 1 ORDER BY last_seen_ns DESC`),
        proposals:
          all(`SELECT p.state, json_extract(p.payload_json, '$.intervention_type') AS tier,
            json_extract(p.payload_json, '$.target') AS target, f.category, p.created_at,
            CASE WHEN p.state = 'applied' THEN p.updated_at END AS applied_at
          FROM proposals AS p JOIN findings AS f ON f.id = p.finding_id ORDER BY p.created_at DESC`),
      };
    } finally {
      db.close();
    }
  };
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
  await add(root);
  return assets;
}

export function createApp(
  overrides: Partial<Dependencies> = {},
): (request: Request) => Promise<Response> {
  const dependencies: Dependencies = {
    query: clickhouse(CLICKHOUSE_URL),
    workflow: sqliteWorkflow(),
    inbox: countInbox,
    assets: new Map(),
    now: () => new Date(),
    ...overrides,
  };
  const { query } = dependencies;
  let cached: { at: number; registry: Promise<Registry> } | null = null;
  const registry = (): Promise<Registry> => {
    const now = dependencies.now().getTime();
    if (!cached || now - cached.at > REGISTRY_TTL_MS) {
      const pending = loadRegistry(query);
      cached = { at: now, registry: pending };
      pending.catch(() => {
        if (cached?.registry === pending) cached = null;
      });
    }
    return cached.registry;
  };

  const countCache = new Map<
    string,
    { at: number; counts: ReturnType<typeof routeCounts> }
  >();
  const cachedRouteCounts = (
    reg: Registry,
    bound: Record<string, string>,
  ): ReturnType<typeof routeCounts> => {
    const now = dependencies.now().getTime();
    const key = `${bound.start}|${bound.end}`;
    const hit = countCache.get(key);
    if (hit && now - hit.at <= ROUTE_COUNT_TTL_MS) return hit.counts;
    const counts = routeCounts(query, reg, bound);
    countCache.set(key, { at: now, counts });
    counts.catch(() => countCache.delete(key));
    for (const [entry, value] of countCache)
      if (
        countCache.size > ROUTE_COUNT_ENTRIES ||
        now - value.at > ROUTE_COUNT_TTL_MS
      )
        countCache.delete(entry);
    return counts;
  };

  async function view(id: ViewId, filters: Filters): Promise<ViewResponse> {
    const started = performance.now();
    const bound = params(filters);
    const reg = await registry();
    const counts = await cachedRouteCounts(reg, bound);
    let data: Record<string, Row[]>;
    if (id === "pipeline") {
      data = await pipelineData(query, reg, bound, counts);
      data.project_sync = (data.project_sync ?? []).map((row) => ({
        ...row,
        inbox_backlog: dependencies.inbox(),
      }));
    } else {
      data = await batch(query, VIEW_QUERIES[id], bound);
      if (id === "interventions") Object.assign(data, dependencies.workflow());
    }
    return {
      view: id,
      filters,
      queriedAt: dependencies.now().toISOString(),
      elapsedMs: Math.round(performance.now() - started),
      data,
      contributions: contributions(reg, counts, id),
    };
  }

  return async (request) => {
    if (!isTrustedRequest(request))
      return json({ error: "untrusted host or origin" }, 403);
    if (request.method !== "GET" && request.method !== "HEAD")
      return json({ error: "method not allowed" }, 405);
    const url = new URL(request.url);
    try {
      if (url.pathname === "/api/registry") return json(await registry());
      if (url.pathname === "/api/view") {
        const id = url.searchParams.get("view") ?? "";
        if (!(VIEW_IDS as readonly string[]).includes(id))
          return badRequest("view must be a known view");
        const filters = parseFilters(url);
        if (filters instanceof Response) return filters;
        return json(await view(id as ViewId, filters));
      }
      if (url.pathname === "/api/session") {
        const harness = url.searchParams.get("harness") ?? "";
        const session = url.searchParams.get("session") ?? "";
        if (!isHarness(harness) || session === "" || session.length > 256)
          return badRequest("harness and session are required");
        const response: SessionResponse = {
          harness,
          session,
          queriedAt: dependencies.now().toISOString(),
          data: await batch(query, SESSION_QUERIES, { harness, session }),
        };
        return json(response);
      }
    } catch (error) {
      return json(
        { error: error instanceof Error ? error.message : "query failed" },
        503,
      );
    }
    if (url.pathname.startsWith("/api/"))
      return json({ error: "not found" }, 404);
    const route = url.pathname.slice(1);
    const assetPath =
      route === "" ||
      route === "session" ||
      (VIEW_IDS as readonly string[]).includes(route)
        ? "/index.html"
        : url.pathname;
    const asset = dependencies.assets.get(assetPath);
    if (!asset) return json({ error: "not found" }, 404);
    return new Response(
      request.method === "HEAD" ? null : await asset.clone().arrayBuffer(),
      { headers: new Headers(asset.headers) },
    );
  };
}

if (import.meta.main) {
  const assets = await loadAssets();
  const app = createApp({ assets });
  Bun.serve({ hostname: ADDRESS, port: PORT, fetch: app });
  // Load the registry before the first view request needs it.
  void app(
    new Request(`http://${ADDRESS}:${PORT}/api/registry`, {
      headers: { host: `${ADDRESS}:${PORT}` },
    }),
  );
  console.log(`Dashboard listening on http://${ADDRESS}:${PORT}`);
}
