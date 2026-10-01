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

/**
 * Where the local SigNoz ClickHouse's HTTP interface is: the
 * `INTROSPECTION_CLICKHOUSE_URL` variable, else `[dashboard] clickhouse_url`, else the
 * http mode's `[signoz] clickhouse_url` in the agent-introspection config
 * (`AGENT_INTROSPECTION_CONFIG`, default `~/.config/agent-introspection/config.toml`).
 * No address is assumed: container runtimes differ in how they expose ClickHouse.
 */
export function clickhouseUrl(
  env: Record<string, string | undefined> = process.env,
  readConfig: (path: string) => string | undefined = (path) =>
    existsSync(path) ? readFileSync(path, "utf8") : undefined,
): string {
  if (env.INTROSPECTION_CLICKHOUSE_URL) return env.INTROSPECTION_CLICKHOUSE_URL;
  const path =
    env.AGENT_INTROSPECTION_CONFIG ||
    join(env.HOME ?? homedir(), ".config/agent-introspection/config.toml");
  const text = readConfig(path);
  const config =
    text === undefined ? {} : (Bun.TOML.parse(text) as ConfigDocument);
  const url = config.dashboard?.clickhouse_url ?? config.signoz?.clickhouse_url;
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
 * Runs several named read-only queries as one HTTP request. Each query becomes
 * a UNION ALL branch that tags its rows with the query name and renders each
 * row as a JSON object, so result schemas can differ. One request per view
 * avoids opening many connections at once: Bun's HTTP client on this machine
 * stalls about one second on some new connections to OrbStack.
 */
export async function batch(
  query: Query,
  named: Record<string, string>,
  params: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<Record<string, Row[]>> {
  const names = Object.keys(named);
  const result: Record<string, Row[]> = Object.fromEntries(
    names.map((name) => [name, []]),
  );
  if (names.length === 0) return result;
  const branches = names.map(
    (name, index) =>
      `SELECT ${index} AS __q, rowNumberInAllBlocks() AS __n, formatRowNoNewline('JSONEachRow', *) AS __row FROM (\n${named[name]}\n)`,
  );
  const rows = await query(
    `SELECT __q, __row FROM (\n${branches.join("\nUNION ALL\n")}\n) ORDER BY __q, __n`,
    params,
    signal,
  );
  for (const row of rows)
    result[names[Number(row.__q)]!]!.push(JSON.parse(String(row.__row)) as Row);
  return result;
}
