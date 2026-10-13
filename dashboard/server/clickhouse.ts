import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { Row } from "../src/contracts";

/** Runs one read-only statement and returns its rows. */
export type Query = (
  sql: string,
  params?: Record<string, string>,
  signal?: AbortSignal,
) => Promise<Row[]>;

type Fetch = (
  input: string | URL,
  init?: RequestInit & { signal?: AbortSignal },
) => Promise<Response>;

export class ClickHouseError extends Error {}

const QUERY_TIMEOUT_MS = 10_000;

/** Basic-auth credentials for an existing SigNoz ClickHouse. */
export type Credentials = { user: string; password: string };

/**
 * Reads optional credentials from `INTROSPECTION_CLICKHOUSE_USER` and
 * `INTROSPECTION_CLICKHOUSE_PASSWORD`. A password alone uses the `default` user.
 */
export function credentialsFromEnv(
  env: Record<string, string | undefined> = process.env,
): Credentials | undefined {
  const user = env.INTROSPECTION_CLICKHOUSE_USER;
  const password = env.INTROSPECTION_CLICKHOUSE_PASSWORD;
  if (!user && !password) return undefined;
  return { user: user || "default", password: password ?? "" };
}

const defaultReadConfig = (path: string): string | undefined =>
  existsSync(path) ? readFileSync(path, "utf8") : undefined;

function configPath(env: Record<string, string | undefined>): string {
  return (
    env.AGENT_INTROSPECTION_CONFIG ||
    join(env.HOME ?? homedir(), ".config/agent-introspection/config.toml")
  );
}

function configuredUrl(text: string | undefined): unknown {
  if (text === undefined) return undefined;
  const config = Bun.TOML.parse(text) as ConfigDocument;
  return config.dashboard?.clickhouse_url ?? config.signoz?.clickhouse_url;
}

/** Loopback, `localhost`, or a local container name such as OrbStack's `*.orb.local`. */
export function assertLocalAddress(baseUrl: string): void {
  let host: string;
  try {
    host = new URL(baseUrl).hostname.toLowerCase();
  } catch {
    throw new Error(`ClickHouse address is not a valid URL: ${baseUrl}`);
  }
  const local =
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host.endsWith(".orb.local") ||
    host === "[::1]" ||
    /^127(\.\d{1,3}){3}$/.test(host);
  if (!local)
    throw new Error(
      `ClickHouse address ${host} is not local (loopback, localhost, or *.orb.local)`,
    );
}

/**
 * Where the local SigNoz ClickHouse's HTTP interface is: the
 * `INTROSPECTION_CLICKHOUSE_URL` variable, else `[dashboard] clickhouse_url`, else the
 * http mode's `[signoz] clickhouse_url` in the agent-introspection config
 * (`AGENT_INTROSPECTION_CONFIG`, default `~/.config/agent-introspection/config.toml`).
 * No address is assumed: container runtimes differ in how they expose ClickHouse.
 */
export function clickhouseUrl(
  env: Record<string, string | undefined> = process.env,
  readConfig: (path: string) => string | undefined = defaultReadConfig,
): string {
  if (env.INTROSPECTION_CLICKHOUSE_URL) return env.INTROSPECTION_CLICKHOUSE_URL;
  const path = configPath(env);
  const url = configuredUrl(readConfig(path));
  if (typeof url === "string" && url !== "") return url;
  throw new Error(
    "No ClickHouse address for the dashboard: set INTROSPECTION_CLICKHOUSE_URL or " +
      `[dashboard] clickhouse_url in ${path}`,
  );
}

type ConfigDocument = {
  dashboard?: { clickhouse_url?: unknown };
  signoz?: { clickhouse_url?: unknown };
};

/**
 * ClickHouse HTTP client for the local SigNoz ClickHouse, with basic-auth
 * credentials when the server needs a login. Every statement runs with
 * `readonly=2` and binds user input as query parameters, never as SQL text.
 */
export function clickhouse(
  baseUrl: string,
  fetchFn: Fetch = fetch,
  credentials?: Credentials,
): Query {
  assertLocalAddress(baseUrl);
  const headers: Record<string, string> = credentials
    ? {
        Authorization: `Basic ${btoa(`${credentials.user}:${credentials.password}`)}`,
      }
    : {};
  return async (sql, params = {}, signal) => {
    const url = new URL(baseUrl);
    url.searchParams.set("readonly", "2");
    url.searchParams.set("default_format", "JSON");
    url.searchParams.set("output_format_json_quote_64bit_integers", "0");
    url.searchParams.set("max_execution_time", String(QUERY_TIMEOUT_MS / 1000));
    for (const [name, value] of Object.entries(params))
      url.searchParams.set(`param_${name}`, value);
    const timeout = AbortSignal.timeout(QUERY_TIMEOUT_MS);
    const response = await fetchFn(url, {
      method: "POST",
      headers,
      body: sql,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    const text = await response.text();
    if (!response.ok)
      throw new ClickHouseError(
        /Code: \d+\. DB::Exception: [^\n]*/.exec(text)?.[0] ??
          text.slice(0, 300),
      );
    return (JSON.parse(text) as { data: Row[] }).data;
  };
}

/** Snapshot tables hold the last 90 days (facts.SNAPSHOT_DAYS). */
export const SNAPSHOT_DAYS = 90;

/**
 * Points queries at the live fact views instead of their 90-day snapshots, for
 * windows that start before the snapshot horizon or rows of any age.
 */
export const live = (queries: Record<string, string>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(queries).map(([name, sql]) => [
      name,
      sql.replace(/introspection\.(\w+)_snapshot\b/g, "introspection.$1"),
    ]),
  );

/**
 * Branches in flight at once. ClickHouse here runs near its memory cap and a UNION
 * ALL's peak memory is the sum of its branches, so branches run apart and a few at
 * a time. A small pool also keeps connections few: Bun's HTTP client on this machine
 * stalls about one second on some new connections to OrbStack, and a reused
 * keep-alive connection avoids the stall.
 */
export const BRANCH_CONCURRENCY = 3;

/** Longest error message kept per failed branch. */
const ERROR_LIMIT = 200;

/** Named results plus a short message for each branch that failed (its rows are empty). */
export type Settled = {
  data: Record<string, Row[]>;
  errors: Record<string, string>;
};

/** A failure as one short line: ClickHouse's exception line, or the error's message. */
export const shortMessage = (error: unknown): string =>
  (error instanceof Error ? error.message : String(error))
    .replace(/\s+/g, " ")
    .slice(0, ERROR_LIMIT);

/**
 * Runs several named read-only queries, each as its own request, at most
 * `BRANCH_CONCURRENCY` at a time. A failing branch does not fail the others: it
 * returns no rows and is named in `errors`.
 */
export async function batchSettled(
  query: Query,
  named: Record<string, string>,
  params: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<Settled> {
  const names = Object.keys(named);
  const data: Record<string, Row[]> = Object.fromEntries(
    names.map((name) => [name, []]),
  );
  const errors: Record<string, string> = {};
  let next = 0;
  const worker = async (): Promise<void> => {
    while (next < names.length) {
      const name = names[next++]!;
      const [outcome] = await Promise.allSettled([
        query(named[name]!, params, signal),
      ]);
      if (outcome.status === "fulfilled") data[name] = outcome.value;
      else errors[name] = shortMessage(outcome.reason);
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(BRANCH_CONCURRENCY, names.length) }, worker),
  );
  return { data, errors };
}

/**
 * Like `batchSettled`, but any failed branch fails the whole call: for data a caller
 * cannot use in part (the registry, route counts, a session).
 */
export async function batch(
  query: Query,
  named: Record<string, string>,
  params: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<Record<string, Row[]>> {
  const { data, errors } = await batchSettled(query, named, params, signal);
  const failed = Object.values(errors)[0];
  if (failed !== undefined) throw new ClickHouseError(failed);
  return data;
}
