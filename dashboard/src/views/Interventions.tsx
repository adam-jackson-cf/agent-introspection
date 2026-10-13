import { DailyChart, DataTable, TableView } from "../charts";
import type { ReactNode } from "react";
import type { Row } from "../contracts";
import { HarnessName, Kpi, Panel, Section, ViewLink } from "../components";
import {
  fmtCount,
  fmtPct,
  harnessLabel,
  num,
  SERIES,
  shortTime,
} from "../format";

const CLUSTER_DETECTOR = "facts.failure_cluster";
const CORRECTION_DETECTOR = "facts.repeated_correction";
const DETECTOR_LABEL: Record<string, string> = {
  [CLUSTER_DETECTOR]: "Failure cluster",
  [CORRECTION_DETECTOR]: "Repeated correction",
};

const strings = (value: unknown): string[] | null =>
  Array.isArray(value) ? value.map(String) : null;

/** A finding's subject as one readable line. */
export const subjectLabel = (row: Row): string =>
  row.kind === "correction"
    ? `${String(row.project ?? "—")} · ${String(row.correction_kind ?? "—").replaceAll("_", " ")}`
    : row.tool_family != null
      ? `${String(row.tool_family)} · ${String(row.failure_class ?? "—")}`
      : String(row.category ?? "—");

/** The structured success metric as one readable line; null for a legacy free-text metric. */
export const metricLabel = (row: Row): string | null =>
  row.metric == null
    ? null
    : `${String(row.metric)} ≤ ${num(row.max_ratio)}× baseline · ${num(row.evaluation_days)} d after vs ${num(row.baseline_days)} d before${
        strings(row.metric_harnesses)
          ? ` · ${strings(row.metric_harnesses)!.map(harnessLabel).join(", ")}`
          : ""
      }`;

const HarnessList = ({ value }: { value: unknown }) => {
  const list = strings(value);
  if (!list || list.length === 0) return <>—</>;
  return (
    <span className="breakdown">
      {list.map((harness) => (
        <HarnessName key={harness} value={harness} />
      ))}
    </span>
  );
};

const fmtRatio = (value: unknown) =>
  value === null || value === undefined ? "—" : `${num(value).toFixed(2)}×`;

const BASELINE_SERIES = [
  {
    key: "failures_rate",
    label: "Cluster failures per 100 tasks",
    color: SERIES[0]!,
  },
  {
    key: "corrected_rate",
    label: "Corrections per 100 labelled tasks",
    color: SERIES[1]!,
  },
];

/** Applied-KPI detail: in-range count plus verdict parts that sum to the headline. */
export const appliedDetail = (kpi: Row): string =>
  `${fmtCount(num(kpi.applied_in_range))} in range · ${fmtCount(num(kpi.validated))} validated · ${fmtCount(num(kpi.regressed))} regressed · ${fmtCount(num(kpi.inconclusive))} inconclusive · ${fmtCount(num(kpi.not_evaluated))} not evaluated`;

/** Where proposals come from: drafted by hand, never on a schedule. */
function NoProposals() {
  return (
    <div className="not-applicable" role="status">
      <b>No proposals yet</b>
      <p>
        Proposals are drafted manually; nothing schedules it. Run{" "}
        <code>
          agent-introspection proposal draft --reserved-model-budget N
        </code>{" "}
        (add <code>--dry-run</code> to preview), then apply one. The
        before-and-after comparison starts once a proposal is applied.
      </p>
    </div>
  );
}

/** The children once a proposal exists; before that, how proposals get drafted. */
function WithProposals(props: { count: number; children: ReactNode }) {
  return props.count === 0 ? <NoProposals /> : props.children;
}

export default function Interventions({
  data,
}: {
  data: Record<string, Row[]>;
}) {
  const kpi = data.kpis?.[0] ?? {};
  const findings = data.findings ?? [];
  const proposals = data.proposals ?? [];
  const history = data.proposal_events ?? [];
  const comparisons = data.comparisons ?? [];
  const corrections = data.corrections ?? [];
  const baseline = data.baseline_daily ?? [];
  const tiers = data.tiers ?? [];
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Active findings"
          value={fmtCount(num(kpi.findings))}
          detail={`${num(kpi.actionable)} actionable · current state, not the selected range`}
          signals={["intervene.findings"]}
        />
        <Kpi
          title="Proposals"
          value={fmtCount(num(kpi.proposals))}
          detail={`${num(kpi.pending)} pending · current state, not the selected range`}
          signals={["intervene.tier_audit"]}
        />
        <Kpi
          title="Applied interventions"
          source={["cluster_daily", "correction_daily", "task_daily"]}
          value={fmtCount(num(kpi.applied))}
          detail={appliedDetail(kpi)}
          signals={["intervene.post_recurrence"]}
        />
        <Kpi
          title="Repeated corrections"
          value={fmtCount(num(kpi.corrections))}
          detail="practices users keep correcting, not yet codified"
          signals={["intervene.practice_recurrence"]}
        />
      </Section>
      <Section title="Did an intervention reduce what it targeted?">
        <Panel
          title="Post-intervention recurrence"
          source={["cluster_daily", "correction_daily", "task_daily"]}
          subtitle="The finding's own cluster or correction per task, equal windows before and after application; the verdict is the one `facts sync` recorded"
          signals={["intervene.post_recurrence"]}
          span={12}
        >
          <WithProposals count={proposals.length}>
            <DataTable
              columns={[
                { key: "target", label: "Target" },
                { key: "subject", label: "Finding" },
                { key: "tier", label: "Tier" },
                {
                  key: "applied_at",
                  label: "Applied (UTC)",
                  render: (row) => shortTime(row.applied_at),
                },
                { key: "days", label: "Window days", numeric: true },
                { key: "pre_matched", label: "Before", numeric: true },
                { key: "pre_tasks", label: "Tasks before", numeric: true },
                { key: "post_matched", label: "After", numeric: true },
                { key: "post_tasks", label: "Tasks after", numeric: true },
                {
                  key: "change",
                  label: "Rate change",
                  numeric: true,
                  render: (row) =>
                    row.change_pct === null || row.change_pct === undefined
                      ? "—"
                      : `${num(row.change_pct).toFixed(0)}%`,
                },
                {
                  key: "verdict",
                  label: "Verdict",
                  render: (row) =>
                    row.verdict == null ? "not evaluated" : String(row.verdict),
                },
                {
                  key: "evaluated_ratio",
                  label: "Recorded ratio",
                  numeric: true,
                  render: (row) => fmtRatio(row.evaluated_ratio),
                },
              ]}
              rows={comparisons}
              empty="No intervention has been applied in the selected range; the comparison starts once a proposal reaches applied."
            />
          </WithProposals>
        </Panel>
        <Panel
          title="Recurrence baseline"
          source="baseline_daily"
          subtitle="Per Europe/London day: failure-cluster failures per 100 attributed tasks, and next-prompt corrections per 100 labelled tasks with a project"
          signals={["intervene.post_recurrence"]}
          span={12}
        >
          <WithProposals count={proposals.length}>
            <>
              <DailyChart
                rows={baseline}
                series={BASELINE_SERIES}
                kind="line"
                format={(value) => (value === null ? "—" : value.toFixed(0))}
              />
              <TableView
                columns={[
                  { key: "day", label: "Day" },
                  ...BASELINE_SERIES.map((entry) => ({
                    key: entry.key,
                    label: entry.label,
                    numeric: true,
                  })),
                ]}
                rows={baseline}
              />
            </>
          </WithProposals>
        </Panel>
      </Section>
      <Section title="Findings and proposals">
        <Panel
          title="Active findings"
          subtitle="Failure clusters and repeated corrections promoted by `facts sync` (7 Europe/London days). Current workflow state, not the selected range"
          signals={["intervene.findings"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "detector",
                label: "Detector",
                render: (row) =>
                  DETECTOR_LABEL[String(row.detector)] ?? String(row.detector),
              },
              {
                key: "subject",
                label:
                  "Tool family · failure class, or project · correction kind",
                render: (row) => subjectLabel(row),
              },
              {
                key: "harnesses",
                label: "Harnesses",
                render: (row) => <HarnessList value={row.harnesses} />,
              },
              {
                key: "impact",
                label: "Impact",
                numeric: true,
                render: (row) =>
                  row.impact == null ? "—" : fmtCount(num(row.impact)),
              },
              { key: "state", label: "State" },
              { key: "occurrences", label: "Count", numeric: true },
              { key: "tasks", label: "Tasks", numeric: true },
              { key: "days", label: "Days", numeric: true },
              {
                key: "last_seen",
                label: "Last seen (UTC)",
                render: (row) => shortTime(row.last_seen),
              },
            ]}
            rows={findings}
            empty="No findings recorded."
          />
          <ViewLink view="recurrence">Open Recurring failures</ViewLink>
        </Panel>
        <Panel
          title="Proposals"
          subtitle="Each proposal's structured success metric and its latest recorded evaluation. Current workflow state, not the selected range"
          signals={["intervene.post_recurrence", "intervene.tier_audit"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "target", label: "Target" },
              {
                key: "subject",
                label: "Finding",
                render: (row) => subjectLabel(row),
              },
              { key: "tier", label: "Tier" },
              { key: "state", label: "State" },
              {
                key: "metric",
                label: "Success metric",
                render: (row) => metricLabel(row) ?? "free text (legacy)",
              },
              {
                key: "applied_at",
                label: "Applied (UTC)",
                render: (row) => shortTime(row.applied_at),
              },
              {
                key: "verdict",
                label: "Verdict",
                render: (row) =>
                  row.verdict == null ? "not evaluated" : String(row.verdict),
              },
              {
                key: "evaluated_ratio",
                label: "Ratio",
                numeric: true,
                render: (row) => fmtRatio(row.evaluated_ratio),
              },
              {
                key: "rates",
                label: "Baseline → evaluation rate",
                numeric: true,
                render: (row) =>
                  row.verdict == null
                    ? "—"
                    : `${fmtPct(row.baseline_rate == null ? null : 100 * num(row.baseline_rate))} → ${fmtPct(row.evaluation_rate == null ? null : 100 * num(row.evaluation_rate))}`,
              },
            ]}
            rows={proposals}
            empty="No proposals yet; they are drafted manually with `agent-introspection proposal draft`."
          />
        </Panel>
        <Panel
          title="Approval history"
          subtitle="Each decision event on the proposals above: who, when, and the recorded reason. Current workflow state, not the selected range"
          signals={["intervene.tier_audit"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "at",
                label: "When (UTC)",
                render: (row) => shortTime(row.at),
              },
              { key: "target", label: "Target" },
              { key: "event", label: "Event" },
              {
                key: "actor",
                label: "Actor",
                render: (row) => (row.actor == null ? "—" : String(row.actor)),
              },
              {
                key: "summary",
                label: "Reason / evidence",
                render: (row) =>
                  row.summary == null ? "—" : String(row.summary),
              },
            ]}
            rows={history}
            empty="No proposal decisions recorded."
          />
        </Panel>
        <Panel
          title="Enforcement-tier audit"
          subtitle="Proposals by selected tier"
          signals={["intervene.tier_audit"]}
        >
          <DataTable
            columns={[
              { key: "tier", label: "Tier" },
              { key: "proposals", label: "Proposals", numeric: true },
            ]}
            rows={tiers}
            empty="No proposals yet."
          />
        </Panel>
        <Panel
          title="Uncodified practice recurrence"
          subtitle="Repeated-correction findings: the same correction in one project across sessions"
          signals={["intervene.practice_recurrence"]}
        >
          <DataTable
            columns={[
              { key: "project", label: "Project" },
              {
                key: "correction_kind",
                label: "Correction",
                render: (row) =>
                  String(row.correction_kind ?? "—").replaceAll("_", " "),
              },
              {
                key: "harnesses",
                label: "Harnesses",
                render: (row) => <HarnessList value={row.harnesses} />,
              },
              { key: "task_types", label: "Task types" },
              { key: "sessions", label: "Sessions", numeric: true },
              { key: "impact", label: "Tasks", numeric: true },
              { key: "state", label: "State" },
            ]}
            rows={corrections}
            empty="No correction repeats in one project across sessions."
          />
        </Panel>
      </Section>
    </>
  );
}
