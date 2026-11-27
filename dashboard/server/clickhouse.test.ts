import { expect, test } from "bun:test";
import { DEFAULT_PORT, dashboardPort } from "./clickhouse";

const ENV = { AGENT_INTROSPECTION_CONFIG: "/etc/ai.toml" };

test("the dashboard port defaults to 4173 without a [dashboard] port", () => {
  expect(dashboardPort(ENV, () => undefined)).toBe(DEFAULT_PORT);
  expect(dashboardPort(ENV, () => "[signoz]\n")).toBe(4173);
});

test("the dashboard port is read from the config file the CLI writes", () => {
  const read: string[] = [];
  const port = dashboardPort(ENV, (path) => {
    read.push(path);
    return "[dashboard]\nport = 5174\n";
  });
  expect(port).toBe(5174);
  expect(read).toEqual(["/etc/ai.toml"]);
});

test.each(["0", "65536", '"5174"', "51.5"])(
  "the dashboard port refuses %s, which is not a TCP port",
  (value) => {
    const config = `[dashboard]\nport = ${value}\n`;
    expect(() => dashboardPort(ENV, () => config)).toThrow(
      "must be a TCP port",
    );
  },
);
