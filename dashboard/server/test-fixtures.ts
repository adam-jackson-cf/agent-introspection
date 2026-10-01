import { countsFromRows } from "./pipeline";
import {
  HARNESSES,
  type Harness,
  type Registry,
  type Row,
} from "../src/contracts";
import { hiddenSignals } from "../src/harness-scope";

export const HOST = { host: "127.0.0.1:4173" };
export const START = "2026-09-21T00:00:00.000Z";
export const END = "2026-09-28T00:00:00.000Z";

const LABELS: Record<Harness, string> = {
  "oh-my-pi": "omp",
  "codex-app-server": "Codex app-server",
  codex_cli_rs: "Codex CLI",
  codex_exec: "Codex exec",
  "claude-code": "Claude Code",
};

/** The registry as this machine sees it: only `enabled` in use, hidden signals derived. */
export const enabling = (
  reg: Registry,
  enabled: readonly Harness[],
): Registry => {
  const harnesses = HARNESSES.map((harness) => ({
    harness,
    label: LABELS[harness],
    enabled: enabled.includes(harness) ? 1 : 0,
  }));
  return { ...reg, harnesses, hidden: hiddenSignals({ ...reg, harnesses }) };
};

/** The registry tables in the order `loadRegistry` batches them, as ClickHouse returns them. */
export const registryRows = (reg: Registry): Row[] =>
  batched(
    [
      reg.signals,
      reg.support,
      reg.routes,
      reg.strays,
      reg.exclusions,
      reg.harnesses,
    ].map((rows) => rows as unknown as Row[]),
  );

/**
 * A registry with one signal: omp and Codex reach it; it is not applicable to
 * Claude Code. Every harness is enabled.
 */
export const registry = (): Registry =>
  ({
    signals: [
      {
        signal: "usage.input_tokens",
        view: "cache",
        view_title: "Cache efficiency",
        title: "Input tokens",
        question: "q",
        unit: "tokens",
        formula: "f",
        scope: "harness",
      },
    ],
    support: [
      {
        signal: "usage.input_tokens",
        harness: "oh-my-pi",
        harness_label: "omp",
        route: "omp.chat",
        alignment: "aligned",
        note: "",
      },
      {
        signal: "usage.input_tokens",
        harness: "codex-app-server",
        harness_label: "Codex app-server",
        route: "codex.usage",
        alignment: "aligned",
        note: "",
      },
      {
        signal: "usage.input_tokens",
        harness: "codex_cli_rs",
        harness_label: "Codex CLI",
        route: "codex.usage",
        alignment: "aligned",
        note: "",
      },
      {
        signal: "usage.input_tokens",
        harness: "codex_exec",
        harness_label: "Codex exec",
        route: "codex.usage",
        alignment: "aligned",
        note: "",
      },
      {
        signal: "usage.input_tokens",
        harness: "claude-code",
        harness_label: "Claude Code",
        route: "",
        alignment: "not applicable",
        note: "n",
      },
    ],
    routes: [
      {
        route: "omp.chat",
        harness: "oh-my-pi",
        harness_label: "omp",
        source: "spans",
        match: "name = 'chat'",
        expect: "rows",
        description: "",
      },
      ...(["codex-app-server", "codex_cli_rs", "codex_exec"] as const).map(
        (harness) => ({
          route: "codex.usage",
          harness,
          harness_label: harness,
          source: "logs" as const,
          match: "event_name = 'codex.sse_event'",
          expect: "rows" as const,
          description: "",
        }),
      ),
    ],
    strays: [
      {
        stray: "claude.chat",
        harness: "claude-code",
        source: "spans",
        match: "name = 'chat'",
        reason: "r",
      },
    ],
    exclusions: [
      {
        signal: "intervene.rule_adherence",
        view: "interventions",
        title: "Rule adherence",
        missing: "omp, Codex app-server, Codex CLI, Codex exec, Claude Code",
        reason: "no producer emits rule triggers",
      },
    ],
    harnesses: HARNESSES.map((harness) => ({
      harness,
      label: LABELS[harness],
      enabled: 1,
    })),
    hidden: [],
  }) as Registry;

/**
 * The base registry plus `provider.throughput`, which omp reaches and Codex does
 * not emit (Claude Code: not applicable here, to keep one route).
 */
export const withThroughput = (): Registry => {
  const base = registry();
  return {
    ...base,
    signals: [
      ...base.signals,
      {
        ...base.signals[0]!,
        signal: "provider.throughput",
        view: "provider",
        title: "Output throughput",
      },
    ],
    support: [
      ...base.support,
      ...base.support.map((entry) => ({
        ...entry,
        signal: "provider.throughput",
        ...(entry.route === "codex.usage"
          ? {
              route: "",
              alignment: "not emitted" as const,
              note: "no per-call duration",
            }
          : {}),
      })),
    ],
  };
};

/** The base registry plus `task.count`, which Codex reaches on its turn spans. */
export const withTaskRoute = (): Registry => {
  const base = registry();
  return {
    ...base,
    signals: [...base.signals, { ...base.signals[0]!, signal: "task.count" }],
    support: [
      ...base.support,
      ...base.support.map((entry) => ({
        ...entry,
        signal: "task.count",
        route: entry.route === "codex.usage" ? "codex.turn" : entry.route,
      })),
    ],
    routes: [
      ...base.routes,
      ...(["codex-app-server", "codex_cli_rs", "codex_exec"] as const).map(
        (harness) => ({
          route: "codex.turn",
          harness,
          harness_label: harness,
          source: "spans" as const,
          match: "name = 'session_task.turn'",
          expect: "rows" as const,
          description: "",
        }),
      ),
    ],
  };
};

/** The base registry plus one activity-hook route (`hook.turn_stop`) for Claude Code. */
export const withHookRoute = (): Registry => {
  const base = registry();
  return {
    ...base,
    signals: [
      ...base.signals,
      {
        ...base.signals[0]!,
        signal: "effort.reasoning_tokens",
        view: "effort",
      },
    ],
    support: [
      ...base.support,
      ...base.support.map((entry) => ({
        ...entry,
        signal: "effort.reasoning_tokens",
        ...(entry.harness === "claude-code"
          ? { route: "hook.turn_stop", alignment: "differs" as const }
          : {}),
      })),
    ],
    routes: [
      ...base.routes,
      {
        route: "hook.turn_stop",
        harness: "claude-code",
        harness_label: "Claude Code",
        source: "hooks",
        match: "event_type = 'turn_stop'",
        expect: "rows",
        description: "",
      },
    ],
  };
};

/** Route counts as `routeCountQueries` would return them per source table. */
export const counting = (
  spans: Row[],
  logs: Row[],
  reg: Registry = registry(),
  hooks: Row[] = [],
) => countsFromRows(reg, { spans, logs, hooks });

/** Rows as a batched ClickHouse request returns them: tagged and JSON-encoded. */
export const batched = (results: Row[][]): Row[] =>
  results.flatMap((rows, index) =>
    rows.map((row) => ({ __q: index, __row: JSON.stringify(row) })),
  );
