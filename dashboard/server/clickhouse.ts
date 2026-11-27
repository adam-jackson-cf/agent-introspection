import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { Row } from "../src/contracts";

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

export type Credentials = { user: string; password: string };

export function credentialsFromEnv(
  env: Record<string, string | undefined> = process.env,
): Credentials | undefined {
  const user = env["INTROSPECTION_CLICKHOUSE_USER"];
  const password = env["INTROSPECTION_CLICKHOUSE_PASSWORD"];
  if (!user && !password) return undefined;
  return { user: user || "default", password: password ?? "" };
}

export const readConfigFile = (path: string): string | undefined =>
  existsSync(path) ? readFileSync(path, "utf8") : undefined;

export function configPath(env: Record<string, string | undefined>): string {
  return (
    env["AGENT_INTROSPECTION_CONFIG"] ||
    join(env["HOME"] ?? homedir(), ".config/agent-introspection/config.toml")
  );
}

function configuredUrl(text: string | undefined): unknown {
  if (text === undefined) return undefined;
  const config = Bun.TOML.parse(text) as ConfigDocument;
  if (config.dashboard?.clickhouse_url !== undefined)
    throw new Error(
      "[dashboard] clickhouse_url is retired: move it to [signoz] clickhouse_url",
    );
  return config.signoz?.clickhouse_url;
}

export const DEFAULT_CLICKHOUSE_URL = "http://127.0.0.1:8123";

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

export function clickhouseUrl(
  env: Record<string, string | undefined> = process.env,
  readConfig: (path: string) => string | undefined = readConfigFile,
): string {
  if (env["INTROSPECTION_CLICKHOUSE_URL"])
    return env["INTROSPECTION_CLICKHOUSE_URL"];
  const url = configuredUrl(readConfig(configPath(env)));
  return typeof url === "string" && url !== "" ? url : DEFAULT_CLICKHOUSE_URL;
}

type ConfigDocument = {
  signoz?: { clickhouse_url?: unknown };
  dashboard?: { clickhouse_url?: unknown };
};

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

export const SNAPSHOT_DAYS = 90;

export const live = (queries: Record<string, string>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(queries).map(([name, sql]) => [
      name,
      sql.replace(/introspection\.(\w+)_snapshot\b/g, "introspection.$1"),
    ]),
  );

export const BRANCH_CONCURRENCY = 3;

const ERROR_LIMIT = 200;

export type Settled = {
  data: Record<string, Row[]>;
  errors: Record<string, string>;
};

export const shortMessage = (error: unknown): string =>
  (error instanceof Error ? error.message : String(error))
    .replace(/\s+/g, " ")
    .slice(0, ERROR_LIMIT);

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
