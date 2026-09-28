import type { RouteId } from "./contracts";

export type PanelPresentation = {
  widgetId: string;
  title: string;
  description: string;
  span: 4 | 5 | 6 | 7 | 8 | 12;
  height: number;
};
type SectionPresentation = { title: string; panels: PanelPresentation[] };
type RoutePresentation = {
  title: string;
  eyebrow: string;
  subtitle: string;
  sections: SectionPresentation[];
};
const panel = (
  widgetId: string,
  title: string,
  span: PanelPresentation["span"],
  height: number,
  description = title,
): PanelPresentation => ({ widgetId, title, description, span, height });

export const PRESENTATION: Record<RouteId, RoutePresentation> = {
  pipeline: {
    title: "Pipeline health",
    eyebrow: "Operational health · completion and source time",
    subtitle:
      "Is producer evidence arriving, correlating, attributing, and completing through the pipeline on time?",
    sections: [
      {
        title: "Completion time · at a glance",
        panels: [
          panel(
            "p1-snapshot",
            "Latest pipeline snapshot",
            12,
            150,
            "Latest canonical completed scan · completion time",
          ),
          panel(
            "p2-outcomes",
            "Scan outcomes",
            7,
            285,
            "Terminal scans · completion time",
          ),
          panel(
            "p2-freshness",
            "Freshness & cadence",
            5,
            285,
            "Latest successful completion",
          ),
        ],
      },
      {
        title: "Extraction-bound · workload and source lag",
        panels: [
          panel(
            "p3-duration",
            "Scan workload & duration",
            6,
            226,
            "p50/p95 · scan duration",
          ),
          panel(
            "p4-source-lag",
            "Source lag by producer",
            6,
            226,
            "Lag reference: extraction upper bound",
          ),
        ],
      },
      {
        title: "Source time · capture and attribution",
        panels: [
          panel(
            "p5-correlation",
            "Producer lifecycle correlation",
            12,
            215,
            "Directional session cohorts",
          ),
          panel(
            "p7-coverage",
            "Attribution coverage",
            6,
            187,
            "Stable activities",
          ),
          panel(
            "p8-diagnostics",
            "Attribution diagnostics",
            6,
            187,
            "Top rejection reasons",
          ),
        ],
      },
    ],
  },
  provider: {
    title: "Provider health",
    eyebrow: "Provider reliability · request and attempt populations",
    subtitle:
      "Is the selected provider and model accepting, serving, streaming, and completing logical requests reliably?",
    sections: [
      {
        title: "Final-attempt terminal time · at a glance",
        panels: [
          panel(
            "r1-outcomes",
            "Logical request outcomes",
            8,
            285,
            "Terminal logical requests",
          ),
          panel(
            "r1-summary",
            "Outcome summary",
            4,
            285,
            "Recomputed from request identities",
          ),
        ],
      },
      {
        title: "Attempts, errors, and latency",
        panels: [
          panel(
            "r2-requests",
            "Provider error composition",
            6,
            250,
            "Failed logical requests · not attempt counts",
          ),
          panel(
            "r3-phases",
            "Latency phases",
            6,
            250,
            "Successful attempt cohorts",
          ),
          panel(
            "r4-retry",
            "Retry behavior",
            5,
            250,
            "Logical requests with all attempts loaded",
          ),
          panel(
            "r6-conformance",
            "Requested → response model conformance",
            7,
            250,
            "Both-known model identities",
          ),
        ],
      },
    ],
  },
  usage: {
    title: "Model usage · Usage & interaction",
    eyebrow: "Model usage · resource and explicit interaction signals",
    subtitle:
      "Which models and resources are used, and where does direct user-correction evidence occur?",
    sections: [
      {
        title: "Request terminal time · usage at a glance",
        panels: [
          panel(
            "m1-provenance",
            "Model calls & provenance",
            12,
            150,
            "Logical requests · project-filtered cohort",
          ),
          panel(
            "m2-request-pressure",
            "Request-accounted token pressure",
            6,
            215,
            "Per-request cohorts · no tool-call inference",
          ),
          panel(
            "m3-friction",
            "Explicit user-friction rate",
            6,
            215,
            "Observable canonical tasks · direct signals only",
          ),
        ],
      },
      {
        title: "Task source time · interaction detail",
        panels: [
          panel(
            "m1-calls",
            "Model calls over time",
            7,
            226,
            "Grouped by requested model",
          ),
          panel(
            "m4-recovery",
            "Correction timing & recovery",
            5,
            226,
            "Explicit-friction tasks with observable outcomes",
          ),
        ],
      },
    ],
  },
  tools: {
    title: "Model usage · Tool execution",
    eyebrow: "Model usage · execution diagnosis",
    subtitle:
      "Which tools, failure fingerprints, repeated attempts, and loops affect work—and do agents recover?",
    sections: [
      {
        title: "Task source time · exception-first overview",
        panels: [
          panel(
            "m5-failure",
            "Tool failure fingerprints",
            12,
            250,
            "Top 50 · failure fingerprints",
          ),
          panel(
            "m6-recovery",
            "Failure recovery",
            6,
            150,
            "Groups with observable task end",
          ),
          panel(
            "m7-repeats",
            "Repeated attempts",
            6,
            150,
            "Same operation repeated in one task",
          ),
        ],
      },
      {
        title: "Execution patterns and policy friction",
        panels: [
          panel(
            "m8-churn",
            "Command churn",
            6,
            215,
            "Distinct commands on one target",
          ),
          panel(
            "m9-loops",
            "Tool loops",
            6,
            215,
            "Contiguous repeated operation cycles",
          ),
          panel(
            "m10-sandbox",
            "Sandbox friction",
            6,
            150,
            "Explicit structured outcomes",
          ),
          panel(
            "m11-bypass",
            "Quality-gate bypass",
            6,
            150,
            "Complete fail → mutate → pass sequences",
          ),
        ],
      },
    ],
  },
  recurrence: {
    title: "Model usage · Recurrence & interventions",
    eyebrow: "Model usage · repeated evidence and codification",
    subtitle:
      "Which failures and successful practices recur, where are they concentrated, and what intervention is appropriate?",
    sections: [
      {
        title: "Finding evidence window · action at a glance",
        panels: [
          panel(
            "m14-findings",
            "Actionable repeated failures",
            6,
            215,
            "Evidence-window findings",
          ),
          panel(
            "m17-practices",
            "Uncodified successful practices",
            6,
            215,
            "Repeated sequences with successful outcomes",
          ),
        ],
      },
      {
        title: "Concentration, adherence, and intervention",
        panels: [
          panel(
            "m13-scope",
            "Scope recurrence",
            7,
            187,
            "Normalized targets across canonical tasks",
          ),
          panel(
            "m16-concentration",
            "Project concentration",
            5,
            187,
            "Attributed occurrences · Unresolved separate",
          ),
          panel(
            "m12-adherence",
            "Skill adherence",
            6,
            215,
            "Applicable observable tasks · versioned rules",
          ),
          panel(
            "m18-audit",
            "Enforcement-tier audit",
            6,
            215,
            "One selected tier per actionable candidate",
          ),
        ],
      },
    ],
  },
};
