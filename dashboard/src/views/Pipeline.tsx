import { DailyChart, DataTable, TableView } from "../charts";
import { HARNESSES, type Harness, type Row } from "../contracts";
import { HarnessName, Kpi, Panel, Section, useView } from "../components";
import {
  fmtCount,
  fmtPct,
  fmtSeconds,
  HARNESS_COLOR,
  HARNESS_LABEL,
  num,
  ratio,
} from "../format";

const STATE_MARK: Record<string, { mark: string; tone: string }> = {
  healthy: { mark: "✓", tone: "good" },
  "no events": { mark: "○", tone: "neutral" },
  idle: { mark: "○", tone: "neutral" },
  "not emitted": { mark: "·", tone: "neutral" },
  "stray (explained)": { mark: "≈", tone: "warning" },
  "possible break": { mark: "!", tone: "serious" },
  "stray (unexplained)": { mark: "✕", tone: "critical" },
};
const UNEXPLAINED = new Set(["possible break", "stray (unexplained)"]);

function StateChip({ state, title }: { state: string; title?: string }) {
  const spec = STATE_MARK[state] ?? { mark: "?", tone: "neutral" };
  return (
    <span className={`state-chip ${spec.tone}`} title={title}>
      <b aria-hidden="true">{spec.mark}</b>
      {state}
    </span>
  );
}

function CoverageGrid({ rows }: { rows: Row[] }) {
  const { filters } = useView();
  const harnesses: readonly Harness[] =
    filters.harness === "" ? HARNESSES : [filters.harness];
  const signals = [...new Map(rows.map((row) => [row.signal, row])).values()];
  const views = [...new Set(signals.map((row) => String(row.view)))];
  const cell = (signal: unknown, harness: Harness) =>
    rows.find((row) => row.signal === signal && row.harness === harness);
  return (
    <div className="table-wrap coverage">
      <table>
        <thead>
          <tr>
            <th>Signal</th>
            {harnesses.map((harness) => (
              <th key={harness}>
                <HarnessName value={harness} />
              </th>
            ))}
          </tr>
        </thead>
        {views.map((view) => (
          <tbody key={view}>
            <tr className="group">
              <th colSpan={harnesses.length + 1}>{view}</th>
            </tr>
            {signals
              .filter((row) => row.view === view)
              .map((row) => (
                <tr key={String(row.signal)}>
                  <th scope="row">{String(row.title)}</th>
                  {harnesses.map((harness) => {
                    const entry = cell(row.signal, harness);
                    return (
                      <td key={harness}>
                        {entry && (
                          <StateChip
                            state={String(entry.state)}
                            title={`${String(entry.route) || "no route"} · ${fmtCount(num(entry.own))} own rows · ${fmtCount(num(entry.foreign))} rows on other routes (${fmtCount(num(entry.unexplained))} unexplained)`}
                          />
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

export default function Pipeline({ data }: { data: Record<string, Row[]> }) {
  const { filters } = useView();
  const inScope = (row: Row) =>
    filters.harness === "" || row.harness === filters.harness;
  const loaders = data.loaders ?? [];
  const failing = loaders.filter((row) => num(row.stale) === 1);
  const freshness = (data.freshness ?? []).filter(inScope);
  const missingFacts = freshness.filter((row) => num(row.facts_missing) === 1);
  const maxLag = freshness.length
    ? Math.max(...freshness.map((row) => num(row.lag_seconds)))
    : null;
  const parity = (data.parity ?? []).filter(inScope);
  const parityOk = parity.filter((row) => num(row.ok) === 1).length;
  const sanitization = data.sanitization ?? [];
  const violations = sanitization.reduce(
    (total, row) =>
      total +
      num(row.forbidden_keys) +
      num(row.long_status) +
      num(row.home_paths),
    0,
  );
  const coverage = (data.coverage ?? []).filter(inScope);
  const unexplained = coverage.filter((row) =>
    UNEXPLAINED.has(String(row.state)),
  );
  const recombination = data.recombination ?? [];
  const daily = new Map<string, Row>();
  for (const row of (data.daily_rows ?? []).filter(inScope)) {
    const day = String(row.day);
    const entry = daily.get(day) ?? { day };
    const key = String(row.harness);
    entry[key] = num(entry[key]) + num(row.n);
    daily.set(day, entry);
  }
  const dailySeries = HARNESSES.filter(
    (harness) => filters.harness === "" || harness === filters.harness,
  ).map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const window = parity[0]
    ? `${String(parity[0].window_start).slice(0, 16)} → ${String(parity[0].window_end).slice(0, 16)} UTC`
    : "";
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Loaders and snapshots"
          value={`${loaders.length - failing.length} / ${loaders.length}`}
          detail={
            failing.length === 0
              ? "on schedule, no errors"
              : `${failing.length} stale or failing`
          }
          signals={["pipeline.loader_refresh"]}
        />
        <Kpi
          title="Max fact lag"
          value={missingFacts.length ? "no facts" : fmtSeconds(maxLag)}
          detail={
            missingFacts.length
              ? `${missingFacts.length} source(s) with SigNoz rows but no fact rows`
              : "SigNoz latest row minus fact latest row"
          }
          signals={["pipeline.freshness"]}
        />
        <Kpi
          title="Coverage"
          value={`${unexplained.length} unexplained`}
          detail={`${coverage.length} signal × harness cells`}
          signals={["pipeline.coverage"]}
        />
        <Kpi
          title="Sanitization"
          value={`${fmtCount(violations)} violations`}
          detail={`${parityOk} / ${parity.length} harnesses match the raw source`}
          signals={["pipeline.sanitization", "pipeline.parity"]}
        />
      </Section>
      <Section title="Is the loader fresh?">
        <Panel
          title="Loader and snapshot refresh"
          subtitle="Refreshable materialized views in the introspection database"
          signals={["pipeline.loader_refresh"]}
        >
          <DataTable
            columns={[
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
            ]}
            rows={loaders}
          />
        </Panel>
        <Panel
          title="Freshness by harness"
          subtitle="Latest row in the last day: SigNoz versus facts"
          signals={["pipeline.freshness"]}
        >
          <DataTable
            columns={[
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
                render: (row) =>
                  `${fmtSeconds(num(row.source_age_seconds))} ago`,
              },
            ]}
            rows={freshness}
            empty="No harness produced rows in the last day."
          />
        </Panel>
        <Panel
          title="Fact rows per day"
          subtitle="Curated spans and logs loaded per harness (UTC days)"
          signals={["pipeline.freshness"]}
          span={12}
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
      </Section>
      <Section title="Is it complete and correct?">
        <Panel
          title="Coverage grid"
          subtitle="Registry versus data for the selected window; hover a cell for its counts"
          signals={["pipeline.coverage"]}
          span={12}
        >
          <ul className="state-legend" aria-label="Coverage states">
            {Object.keys(STATE_MARK).map((state) => (
              <li key={state}>
                <StateChip state={state} />
              </li>
            ))}
          </ul>
          <CoverageGrid rows={coverage} />
        </Panel>
        <Panel
          title="Source parity"
          subtitle={`usage_events versus a direct SigNoz recount · ${window}`}
          signals={["pipeline.parity"]}
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
            rows={parity}
          />
        </Panel>
        <Panel
          title="All = Σ harnesses"
          subtitle="Direct All aggregate versus the sum of per-harness aggregates"
          signals={["pipeline.recombination"]}
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
            rows={recombination}
          />
        </Panel>
        <Panel
          title="Sanitization"
          subtitle="Stored rows holding raw text, identity keys, or home paths"
          signals={["pipeline.sanitization"]}
        >
          <DataTable
            columns={[
              { key: "source", label: "Table" },
              { key: "rows", label: "Rows", numeric: true },
              { key: "forbidden_keys", label: "Dropped keys", numeric: true },
              { key: "long_status", label: "Status > 160", numeric: true },
              { key: "home_paths", label: "Home paths", numeric: true },
            ]}
            rows={sanitization}
          />
        </Panel>
        <Panel
          title="Project attribution"
          subtitle="Tasks whose session has a project from the session-context hooks"
          signals={["pipeline.project_attribution"]}
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
                key: "share",
                label: "Share",
                numeric: true,
                render: (row) =>
                  fmtPct(ratio(num(row.attributed), num(row.tasks)), 0),
              },
            ]}
            rows={(data.project_coverage ?? []).filter(inScope)}
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
              { key: "inbox_backlog", label: "Inbox backlog", numeric: true },
              { key: "events", label: "Hook events", numeric: true },
            ]}
            rows={data.project_sync ?? []}
          />
          <DataTable
            columns={[
              { key: "producer", label: "Hook" },
              { key: "reason_code", label: "Rejected because" },
              { key: "n", label: "Events", numeric: true },
            ]}
            rows={data.project_rejections ?? []}
            empty="No hook rejections in the selected range."
          />
        </Panel>
        <Panel
          title="Stray and unrouted rows"
          subtitle="Registered strays, then rows no route claims (not used by any signal)"
          signals={["pipeline.coverage"]}
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
            rows={(data.strays ?? []).filter(inScope)}
          />
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "source", label: "Source" },
              {
                key: "name",
                label: "Span or event",
                render: (row) => String(row.name) || "(no event name)",
              },
              { key: "n", label: "Rows", numeric: true },
            ]}
            rows={(data.unrouted ?? []).filter(inScope)}
            empty="Every row is claimed by a route or a registered stray."
          />
        </Panel>
      </Section>
    </>
  );
}
