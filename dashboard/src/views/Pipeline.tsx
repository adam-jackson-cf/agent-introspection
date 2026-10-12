import { DailyChart, DataTable, TableView, type Column } from "../charts";
import { VIEW_IDS, type Harness, type Row, type ViewId } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  Section,
  ShowLink,
  useView,
  useSelectedHarnesses,
  ViewLink,
} from "../components";
import { presetFor } from "../app-state";

import { OTHER_TOKEN } from "../tokens";
import {
  fmtCount,
  fmtPct,
  fmtSeconds,
  HARNESS_COLOR,
  HARNESS_LABEL,
  maybe,
  num,
} from "../format";
import { CoverageSummary, StateChip } from "./PipelineCoverage";

/** Hook rows whose harness could not be resolved chart as their own series. */
const UNRESOLVED_KEY = "unresolved";
const UNRESOLVED_LABEL = "unresolved (Codex hook)";

/** Long to wide for the chart: one entry per day, one value per series. */
export function dailyByHarness(rows: Row[]) {
  const daily = new Map<string, Row>();
  for (const row of rows) {
    const day = String(row.day);
    const entry = daily.get(day) ?? { day };
    entry[row.harness === "" ? UNRESOLVED_KEY : String(row.harness)] = row.n;
    daily.set(day, entry);
  }
  return daily;
}

function dailySeriesOf(selected: readonly Harness[], dailyRows: Row[]) {
  const series = selected.map((harness) => ({
    key: harness as string,
    label: HARNESS_LABEL[harness] as string,
    color: HARNESS_COLOR[harness] as string,
  }));
  if (dailyRows.some((row) => row.harness === ""))
    series.push({
      key: UNRESOLVED_KEY,
      label: UNRESOLVED_LABEL,
      color: OTHER_TOKEN,
    });
  return series;
}

type Data = Record<string, Row[]>;

/** Which window each figure covers, in a few words (docs/ux/DESIGN.md: every KPI states its period). */
const PERIOD = {
  loaders: "current",
  freshness: "last day",
  parity: "up to 7 days",
  sanitization: "all time",
} as const;

function CoverageKpi({ data }: { data: Data }) {
  const { filters } = useView();
  const summary = data.coverage_summary?.[0] ?? {};
  const unrouted = data.unrouted_summary?.[0] ?? {};
  const unroutedText = `${fmtCount(num(unrouted.rows))} unrouted rows, not used by any signal`;
  return (
    <Kpi
      title="Coverage"
      value={`${num(summary.unexplained)} unexplained`}
      detail={`${num(summary.cells)} signal × harness cells · ${unroutedText} · ${presetFor(filters).toLowerCase()}`}
      signals={["pipeline.coverage"]}
      source={["coverage", "unrouted"]}
      span={1}
    >
      <ViewLink view="pipeline/coverage">Open coverage grid</ViewLink>
    </Kpi>
  );
}

function GlanceBand({ data }: { data: Data }) {
  const summaryOf = (key: string): Row => data[key]?.[0] ?? {};
  const loaderSummary = summaryOf("loader_summary");
  const freshnessSummary = summaryOf("freshness_summary");
  const paritySummary = summaryOf("parity_summary");
  const sanitizationAll =
    (data.sanitization ?? []).find((row) => row.source === "") ?? {};
  const missingFacts = num(freshnessSummary.missing_facts);
  const maxLag =
    freshnessSummary.max_lag_seconds == null
      ? null
      : num(freshnessSummary.max_lag_seconds);
  const compared = num(paritySummary.harnesses);
  return (
    <Section title="At a glance" columns={5}>
      <Kpi
        title="Loaders and snapshots"
        value={`${num(loaderSummary.ok)} / ${num(loaderSummary.total)}`}
        detail={`${
          num(loaderSummary.failing) === 0
            ? "on schedule, no errors"
            : `${num(loaderSummary.failing)} stale or failing`
        } · ${PERIOD.loaders}`}
        signals={["pipeline.loader_refresh"]}
        source={["loaders", "loader_summary"]}
        span={1}
      >
        <ShowLink target="loader-refresh">Show loader refresh</ShowLink>
      </Kpi>
      <Kpi
        title="Max fact lag"
        value={missingFacts ? "no facts" : fmtSeconds(maxLag)}
        detail={`${
          missingFacts
            ? `${missingFacts} source(s) with SigNoz rows but no fact rows`
            : "SigNoz latest row minus fact latest row"
        } · ${PERIOD.freshness}`}
        signals={["pipeline.freshness"]}
        source={["freshness", "freshness_summary"]}
        span={1}
      />
      <CoverageKpi data={data} />
      <Kpi
        title="Source parity"
        value={
          compared === 0 ? "—" : `${num(paritySummary.matching)} / ${compared}`
        }
        detail={`${
          compared === 0
            ? "no harness with usage rows to compare against the raw source"
            : "harnesses match the raw source"
        } · ${PERIOD.parity}`}
        signals={["pipeline.parity"]}
        source={["parity", "parity_summary"]}
        span={1}
      >
        <ShowLink target="source-parity">Show source parity</ShowLink>
      </Kpi>
      <Kpi
        title="Sanitization"
        value={`${fmtCount(num(sanitizationAll.violations))} violations`}
        detail={`flagged stored rows · ${PERIOD.sanitization}`}
        signals={["pipeline.sanitization"]}
        source="sanitization"
        span={1}
      >
        <ShowLink target="sanitization">Show sanitization</ShowLink>
      </Kpi>
    </Section>
  );
}

/**
 * A table whose rows are mostly fine: the server's one-line summary, only the
 * exception rows, and the full table in a disclosure in place.
 */
function ExceptionTable(props: {
  summary: string;
  columns: Column[];
  exceptions: Row[];
  all: Row[];
  empty: string;
}) {
  const { summary, columns, exceptions, all, empty } = props;
  if (all.length === 0) return <p className="empty">{empty}</p>;
  return (
    <>
      <p className="exception-summary">{summary}</p>
      {exceptions.length > 0 && (
        <DataTable columns={columns} rows={exceptions} />
      )}
      <details className="table-view">
        <summary>Show all {all.length}</summary>
        <DataTable columns={columns} rows={all} />
      </details>
    </>
  );
}

const LOADER_COLUMNS: Column[] = [
  { key: "view", label: "View" },
  { key: "status", label: "Status" },
  { key: "last_success", label: "Last success (UTC)" },
  {
    key: "age_seconds",
    label: "Age",
    numeric: true,
    render: (row) => fmtSeconds(num(row.age_seconds)),
  },
  { key: "written_rows", label: "Rows written", numeric: true },
  {
    key: "exception",
    label: "Error",
    render: (row) => String(row.exception) || "—",
  },
];

const FRESHNESS_COLUMNS: Column[] = [
  {
    key: "harness",
    label: "Harness",
    render: (row) => <HarnessName value={row.harness} />,
  },
  { key: "source", label: "Source" },
  {
    key: "source_latest",
    label: "SigNoz latest",
    render: (row) => String(row.source_latest).slice(0, 19),
  },
  {
    key: "lag_seconds",
    label: "Fact lag",
    numeric: true,
    render: (row) =>
      num(row.facts_missing) === 1
        ? "no facts"
        : fmtSeconds(num(row.lag_seconds)),
  },
  {
    key: "source_age_seconds",
    label: "Last activity",
    numeric: true,
    render: (row) => `${fmtSeconds(num(row.source_age_seconds))} ago`,
  },
];

/** Server summary figures as the one-line summaries above the exception rows. */
const loaderSummaryText = (summary: Row) => {
  const failing = num(summary.failing);
  return `${num(summary.ok)} on schedule${failing > 0 ? `, ${failing} stale or failing` : ""}`;
};

const freshnessSummaryText = (summary: Row) => {
  const sources = num(summary.sources);
  const missing = num(summary.missing_facts);
  return `${sources} sources, ${missing === 0 ? "all with facts" : `${sources - missing} with facts`}`;
};

function FreshnessBand({ data }: { data: Data }) {
  return (
    <Section title="Is the loader fresh?">
      <Panel
        id="loader-refresh"
        title="Loader and snapshot refresh"
        subtitle="Refreshable materialized views in the introspection database"
        signals={["pipeline.loader_refresh"]}
        source={["loaders", "loader_summary"]}
      >
        <ExceptionTable
          summary={loaderSummaryText(data.loader_summary?.[0] ?? {})}
          columns={LOADER_COLUMNS}
          exceptions={data.loader_exceptions ?? []}
          all={data.loaders ?? []}
          empty="No loader reported."
        />
      </Panel>
      <Panel
        title="Freshness by harness"
        subtitle="Latest row in the last day: SigNoz versus facts"
        signals={["pipeline.freshness"]}
        source={["freshness", "freshness_summary"]}
      >
        <ExceptionTable
          summary={freshnessSummaryText(data.freshness_summary?.[0] ?? {})}
          columns={FRESHNESS_COLUMNS}
          exceptions={data.freshness_exceptions ?? []}
          all={data.freshness ?? []}
          empty="No harness produced rows in the last day."
        />
      </Panel>
    </Section>
  );
}

function DailyPanel({ data }: { data: Data; signals: string[] }) {
  const selected = useSelectedHarnesses();
  const dailyRows = data.daily_rows ?? [];
  const daily = dailyByHarness(dailyRows);
  const dailySeries = dailySeriesOf(selected, dailyRows);
  return (
    <Panel
      title="Fact rows per day"
      subtitle="Curated spans, logs, and activity-hook events loaded per harness (UTC days) · rows per day"
      signals={["pipeline.freshness"]}
      source="daily_rows"
      span={8}
    >
      <DailyChart
        rows={[...daily.values()]}
        series={dailySeries}
        kind="stack"
        format={(value) => fmtCount(value)}
      />
      <TableView
        columns={[
          { key: "day", label: "Day" },
          ...dailySeries.map((entry) => ({
            key: entry.key,
            label: entry.label,
            numeric: true,
          })),
        ]}
        rows={[...daily.values()]}
      />
    </Panel>
  );
}

function checksPanels(data: Data) {
  const window = String(data.parity_summary?.[0]?.window_label ?? "");
  return (
    <>
      <Panel
        id="source-parity"
        title="Source parity"
        subtitle={`usage_events versus a direct SigNoz recount · ${window}`}
        signals={["pipeline.parity"]}
        source={["parity", "parity_summary"]}
      >
        <DataTable
          columns={[
            {
              key: "harness",
              label: "Harness",
              render: (row) => <HarnessName value={row.harness} />,
            },
            { key: "raw_operations", label: "Raw ops", numeric: true },
            { key: "fact_operations", label: "Fact ops", numeric: true },
            {
              key: "raw_input",
              label: "Raw input",
              numeric: true,
              render: (row) => fmtCount(num(row.raw_input)),
            },
            {
              key: "fact_input",
              label: "Fact input",
              numeric: true,
              render: (row) => fmtCount(num(row.fact_input)),
            },
            {
              key: "ok",
              label: "Match",
              render: (row) => (
                <StateChip
                  state={num(row.ok) === 1 ? "healthy" : "possible break"}
                />
              ),
            },
          ]}
          rows={data.parity ?? []}
        />
      </Panel>
      <Panel
        title="All = Σ harnesses"
        subtitle="Direct All aggregate versus the sum of per-harness aggregates"
        signals={["pipeline.recombination"]}
        source="recombination"
      >
        <DataTable
          columns={[
            { key: "check", label: "Fact" },
            { key: "measure", label: "Measure" },
            { key: "all", label: "All", numeric: true },
            { key: "sum_of_harnesses", label: "Σ harnesses", numeric: true },
            {
              key: "ok",
              label: "Match",
              render: (row) => (
                <StateChip state={row.ok ? "healthy" : "possible break"} />
              ),
            },
          ]}
          rows={data.recombination ?? []}
        />
      </Panel>
    </>
  );
}

function attributionPanels(data: Data) {
  return (
    <>
      <Panel
        id="sanitization"
        title="Sanitization"
        subtitle="Stored rows holding raw text, identity keys, or home paths · all time"
        signals={["pipeline.sanitization"]}
        source="sanitization"
      >
        <DataTable
          columns={[
            { key: "source", label: "Table" },
            { key: "rows", label: "Rows", numeric: true },
            { key: "forbidden_keys", label: "Dropped keys", numeric: true },
            { key: "long_status", label: "Text > 160", numeric: true },
            { key: "home_paths", label: "Home paths", numeric: true },
          ]}
          rows={(data.sanitization ?? []).filter((row) => row.source !== "")}
        />
      </Panel>
      <Panel
        title="Project attribution"
        subtitle="Tasks whose session has a Git project, resolved from the harness session stores · selected range"
        signals={["pipeline.project_attribution"]}
        source={["project_coverage", "project_sync", "project_rejections"]}
      >
        <DataTable
          columns={[
            {
              key: "harness",
              label: "Harness",
              render: (row) => <HarnessName value={row.harness} />,
            },
            { key: "tasks", label: "Tasks", numeric: true },
            { key: "attributed", label: "With project", numeric: true },
            {
              key: "no_session_record",
              label: "No session record",
              numeric: true,
            },
            {
              key: "share",
              label: "Share",
              numeric: true,
              render: (row) => fmtPct(maybe(row.share_rate), 0),
            },
          ]}
          rows={data.project_coverage ?? []}
        />
        <DataTable
          columns={[
            {
              key: "last_load",
              label: "Last sync (UTC)",
              render: (row) => String(row.last_load).slice(0, 19),
            },
            {
              key: "age_seconds",
              label: "Age",
              numeric: true,
              render: (row) => fmtSeconds(num(row.age_seconds)),
            },
            {
              key: "inbox_backlog",
              label: "Hook inbox backlog",
              numeric: true,
            },
            { key: "events", label: "Sessions resolved", numeric: true },
          ]}
          rows={data.project_sync ?? []}
          label="Project attribution: last sync"
        />
        <DataTable
          columns={[
            { key: "store", label: "Session store" },
            { key: "reason", label: "No project because" },
            { key: "n", label: "Sessions", numeric: true },
          ]}
          rows={data.project_rejections ?? []}
          label="Project attribution: sessions without a project"
          empty="Every session in the selected range has a project."
        />
        <ViewLink view="recurrence">Open Recurrence</ViewLink>
      </Panel>
    </>
  );
}

/** A registry signal's view as a link to its page, falling back to the key. */
function SignalView({ view }: { view: string }) {
  const { registry } = useView();
  const known = (VIEW_IDS as readonly string[]).includes(view);
  const title =
    registry.signals.find((entry) => entry.view === view)?.view_title ?? view;
  return known ? <ViewLink view={view as ViewId}>{title}</ViewLink> : title;
}

function RegistryLists({ data }: { data: Data }) {
  return (
    <details className="disclosure-band">
      <summary>
        Registry lists: stray and unrouted rows, excluded signals, signals
        hidden on this machine
      </summary>
      <div className="grid">
        <Panel
          title="Stray and unrouted rows"
          subtitle="Registered strays, then rows no route claims (not used by any signal)"
          signals={["pipeline.coverage"]}
          source="unrouted"
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "source", label: "Source" },
              { key: "reason", label: "Explanation" },
            ]}
            rows={data.strays ?? []}
            label="Stray and unrouted rows: registered strays"
          />
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) =>
                  row.harness === "" ? (
                    UNRESOLVED_LABEL
                  ) : (
                    <HarnessName value={row.harness} />
                  ),
              },
              { key: "source", label: "Source" },
              {
                key: "name",
                label: "Span or event",
                render: (row) => String(row.name) || "(no event name)",
              },
              { key: "n", label: "Rows", numeric: true },
            ]}
            rows={data.unrouted ?? []}
            total={num(data.unrouted_summary?.[0]?.names)}
            empty="Every row is claimed by a route or a registered stray."
          />
        </Panel>
        <Panel
          title="Excluded signals"
          subtitle="Signals left off the dashboard because no harness produces them, natively or through an activity hook"
          signals={["pipeline.coverage"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "title", label: "Signal" },
              {
                key: "view",
                label: "View",
                render: (row) => <SignalView view={String(row.view)} />,
              },
              { key: "missing", label: "Missing for" },
              { key: "reason", label: "Reason" },
            ]}
            rows={data.exclusions ?? []}
            empty="No signal is excluded: some harness produces every signal."
          />
        </Panel>
        <Panel
          title="Signals hidden on this machine"
          subtitle="An enabled harness does not emit them, so their panels are not shown here; a machine without that harness shows them"
          signals={["pipeline.coverage"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "title", label: "Signal" },
              {
                key: "view",
                label: "View",
                render: (row) => <SignalView view={String(row.view)} />,
              },
              { key: "missing", label: "Not emitted by" },
              { key: "note", label: "Why" },
            ]}
            rows={data.hidden ?? []}
            empty="No signal is hidden: every enabled harness reaches every signal."
          />
        </Panel>
      </div>
    </details>
  );
}

export default function Pipeline({ data }: { data: Data }) {
  return (
    <>
      <GlanceBand data={data} />
      <FreshnessBand data={data} />
      <Section title="Is it complete and correct?">
        <DailyPanel data={data} signals={["pipeline.freshness"]} />
        <CoverageSummary
          data={data}
          span={4}
          linked
          signals={["pipeline.coverage"]}
        />
        {checksPanels(data)}
        {attributionPanels(data)}
      </Section>
      <RegistryLists data={data} />
    </>
  );
}
