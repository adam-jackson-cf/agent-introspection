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
