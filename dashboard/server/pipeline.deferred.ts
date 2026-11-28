import { describe, expect, test } from "bun:test";
import { type Row } from "../src/contracts";
import {
  ORDERED_TOTALS,
  SENT_FROM_TOTALS,
  spoolDelivery,
} from "./record-route";

const CONTAINER_SETTING = "AGENT_INTROSPECTION_TEST_CLICKHOUSE";

const container = (): string => {
  const name = process.env[CONTAINER_SETTING];
  if (!name) {
    throw new Error(
      `${CONTAINER_SETTING} names no ClickHouse container; run bash scripts/run-ci-quality-gates.sh install, which starts one`,
    );
  }
  return name;
};

const sentFor = async (totals: number[]): Promise<number> => {
  const rows = totals
    .map((total, index) => `(${index + 1}, ${total})`)
    .toReversed()
    .join(", ");
  const sql = `SELECT ${SENT_FROM_TOTALS("totals")} AS sent FROM (SELECT ${ORDERED_TOTALS} AS totals FROM values('produced_ns UInt64, sent_total Int64', ${rows})) FORMAT TSV`;
  const proc = Bun.spawn(
    ["docker", "exec", "-i", container(), "clickhouse-client"],
    {
      stdin: new TextEncoder().encode(sql),
      stdout: "pipe",
      stderr: "pipe",
      timeout: 60000,
    },
  );
  const [out, code] = await Promise.all([
    new Response(proc.stdout).text(),
    proc.exited,
  ]);
  if (code !== 0) throw new Error(await new Response(proc.stderr).text());
  return Number(out.trim());
};

const ns = (ago: number) =>
  `toUInt64(toUnixTimestamp(now() - INTERVAL ${ago} SECOND)) * 1000000000`;

const logRow = (
  kind: "beat" | "record",
  host: string,
  observedAgo: number,
  producedAgo: number,
  sentTotal = 0,
): string => {
  const body =
    kind === "beat"
      ? `{"schema":"agent-introspection.spool-status/1","sent_total":{"hook-event":${sentTotal},"session-project":0}}`
      : `{"schema":"agent-introspection.hook-event/1"}`;
  const event =
    kind === "beat"
      ? "agent_introspection.spool_status"
      : "agent_introspection.hook_event";
  return `SELECT ${ns(producedAgo)} AS timestamp, ${ns(observedAgo)} AS observed_timestamp, '${body}' AS body, map('service.name', 'agent-introspection', 'host.name', '${host}') AS resources_string, map('event.name', '${event}') AS attributes_string`;
};

const deliveryFor = async (rows: string[]): Promise<Row[]> => {
  const sql = `${spoolDelivery(`(${rows.join(" UNION ALL ")})`)} FORMAT JSONEachRow`;
  const proc = Bun.spawn(
    ["docker", "exec", "-i", container(), "clickhouse-client"],
    {
      stdin: new TextEncoder().encode(sql),
      stdout: "pipe",
      stderr: "pipe",
      timeout: 60000,
    },
  );
  const [out, code] = await Promise.all([
    new Response(proc.stdout).text(),
    proc.exited,
  ]);
  if (code !== 0) throw new Error(await new Response(proc.stderr).text());
  return out
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Row);
};

describe("spool delivery against a ClickHouse server", () => {
  test("sent sums positive counter increments and counts a post-reset value as its own increment", async () => {
    expect(await sentFor([100, 0, 150])).toBe(150);
    expect(await sentFor([100, 120, 0, 30])).toBe(50);
    expect(await sentFor([10, 20, 35])).toBe(25);
    expect(await sentFor([40])).toBe(0);
  }, 120000);

  test("SPOOL_DELIVERY counts a window by arrival, keeps old-producer records, and ignores shipping order", async () => {
    const hour = 3600;
    const beats = [
      logRow("beat", "h", 5 * hour, 5 * hour, 100),
      logRow("beat", "h", 3 * hour, 3 * hour, 160),
      logRow("beat", "h", 2 * hour, 4 * hour, 130),
    ];
    const records = [
      logRow("record", "h", 3.5 * hour, 3 * 24 * hour),
      logRow("record", "h", 3.5 * hour, 3.5 * hour),
      logRow("record", "h", 6 * hour, 6 * hour),
    ];
    const rows = await deliveryFor([...beats, ...records]);
    const hook = rows.find((row) => row["family"] === "hook-event");
    expect(Number(hook!["sent"])).toBe(60);
    expect(Number(hook!["received"])).toBe(2);
  }, 120000);
});
