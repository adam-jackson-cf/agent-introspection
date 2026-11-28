import type { Features } from "../src/contracts";
import { configPath, readConfigFile } from "./clickhouse";

export const FEATURE_SIGNALS: Record<keyof Features, readonly string[]> = {
  intervention_outcomes: ["intervene.post_recurrence"],
};

export function gatedSignals(features: Features): Set<string> {
  return new Set(
    (Object.keys(FEATURE_SIGNALS) as (keyof Features)[])
      .filter((feature) => !features[feature])
      .flatMap((feature) => FEATURE_SIGNALS[feature]),
  );
}

type ConfigDocument = { experimental?: Record<string, unknown> };

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
