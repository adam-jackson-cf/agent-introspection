import type { Features } from "../src/contracts";
import { configPath, readConfigFile } from "./clickhouse";

/** The registry signals each experimental feature adds; off, they leave the registry. */
export const FEATURE_SIGNALS: Record<keyof Features, readonly string[]> = {
  intervention_outcomes: ["intervene.post_recurrence"],
};

/** The signals of every experimental feature that is off. */
export function gatedSignals(features: Features): Set<string> {
  return new Set(
    (Object.keys(FEATURE_SIGNALS) as (keyof Features)[])
      .filter((feature) => !features[feature])
      .flatMap((feature) => FEATURE_SIGNALS[feature]),
  );
}

type ConfigDocument = { experimental?: Record<string, unknown> };

/**
 * The experimental features `[experimental]` in the agent-introspection config turns
 * on; each is off unless set to true. The CLI validates the table (config.py).
 */
export function experimentalFeatures(
  env: Record<string, string | undefined> = process.env,
  readConfig: (path: string) => string | undefined = readConfigFile,
): Features {
  const text = readConfig(configPath(env));
  const experimental =
    text === undefined
      ? undefined
      : (Bun.TOML.parse(text) as ConfigDocument).experimental;
  return {
    intervention_outcomes: experimental?.["intervention_outcomes"] === true,
  };
}
