import { describe, expect, test } from "bun:test";
import { createApp } from "../server";
import type { Query } from "./clickhouse";
import { experimentalFeatures } from "./features";
import { END, HOST, registry, registryRows, START } from "./test-fixtures";
import type { Registry } from "../src/contracts";

/** The fixture registry plus the outcomes feature's system signal. */
const withOutcomes = (): Registry => {
  const reg = registry();
  return {
    ...reg,
    signals: [
      ...reg.signals,
      {
        ...reg.signals[0]!,
        signal: "intervene.post_recurrence",
        view: "interventions",
        view_title: "Findings and fixes",
        title: "Post-intervention recurrence",
        scope: "system",
      },
    ],
  };
};

describe("experimental features", () => {
  test("each is off unless the config sets it to true", () => {
    const env = { AGENT_INTROSPECTION_CONFIG: "/config.toml" };
    const read = (text: string | undefined) => () => text;
    expect(experimentalFeatures(env, read(undefined))).toEqual({
      intervention_outcomes: false,
    });
    expect(
      experimentalFeatures(env, read("[signoz]\nclickhouse_url = 'x'\n")),
    ).toEqual({ intervention_outcomes: false });
    expect(
      experimentalFeatures(
        env,
        read("[experimental]\nintervention_outcomes = true\n"),
      ),
    ).toEqual({ intervention_outcomes: true });
  });

  test("intervention outcomes stay off the registry and the view until the config turns them on", async () => {
    const seen: string[] = [];
    const query: Query = async (sql) => {
      seen.push(sql);
      return registryRows(withOutcomes(), sql);
    };
    const get = async (
      intervention_outcomes: boolean,
      path: string,
    ): Promise<Record<string, unknown>> => {
      const app = createApp({
        query,
        workflow: () => ({}),
        features: { intervention_outcomes },
      });
      const response = await app(
        new Request(`http://127.0.0.1:4173${path}`, { headers: HOST }),
      );
      return (await response.json()) as Record<string, unknown>;
    };
    const view = `/api/view?view=interventions&start=${START}&end=${END}&harness=`;
    const signalIds = (reg: Record<string, unknown>) =>
      (reg.signals as { signal: string }[]).map((entry) => entry.signal);
    const registryOff = await get(false, "/api/registry");
    expect(registryOff.features).toEqual({ intervention_outcomes: false });
    expect(signalIds(registryOff)).not.toContain("intervene.post_recurrence");
    expect(signalIds(await get(true, "/api/registry"))).toContain(
      "intervene.post_recurrence",
    );
    const off = (await get(false, view)).data as Record<string, unknown>;
    expect(Object.keys(off).sort()).toEqual([
      "findings",
      "kpis",
      "proposal_events",
      "proposals",
      "tiers",
    ]);
    expect(seen.some((sql) => sql.includes("AS failures_rate"))).toBe(false);
    const on = (await get(true, view)).data as Record<string, unknown>;
    expect(on.comparisons).toEqual([]);
    expect(on.baseline_daily).toBeDefined();
  });
});
