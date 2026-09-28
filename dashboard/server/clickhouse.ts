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

/**
 * ClickHouse HTTP client. OrbStack exposes the SigNoz ClickHouse container at
 * `signoz-clickhouse.orb.local` to this Mac only, so no port is published and
 * the SigNoz stack is untouched. Every statement runs with `readonly=2` and
 * binds user input as query parameters, never as SQL text.
 */
export function clickhouse(baseUrl: string, fetchFn: Fetch = fetch): Query {
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
