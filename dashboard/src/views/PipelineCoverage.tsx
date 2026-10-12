import { DataTable } from "../charts";
import { HARNESSES, type Harness, type Row } from "../contracts";
import { HarnessName, Panel, Section, useView, ViewLink } from "../components";
import { fmtCount, num } from "../format";

export const STATE_MARK: Record<string, { mark: string; tone: string }> = {
  healthy: { mark: "✓", tone: "good" },
  "no events": { mark: "○", tone: "neutral" },
  idle: { mark: "○", tone: "neutral" },
  "not applicable": { mark: "·", tone: "neutral" },
  "stray (explained)": { mark: "≈", tone: "warning" },
  "possible break": { mark: "!", tone: "serious" },
  "stray (unexplained)": { mark: "✕", tone: "critical" },
  "not in use": { mark: "–", tone: "neutral" },
  "not in use, rows present": { mark: "!", tone: "warning" },
};

export function StateChip({ state }: { state: string }) {
  const spec = STATE_MARK[state] ?? { mark: "?", tone: "neutral" };
  return (
    <span className={`state-chip ${spec.tone}`}>
      <b aria-hidden="true">{spec.mark}</b>
      {state}
    </span>
  );
}

type CoverageCell = (signal: unknown, harness: Harness) => Row | undefined;

function CoverageRow(props: {
  row: Row;
  harnesses: readonly Harness[];
  cell: CoverageCell;
}) {
  const { row, harnesses, cell } = props;
  return (
    <tr>
      <th scope="row">{String(row.title)}</th>
      {harnesses.map((harness) => {
        const entry = cell(row.signal, harness);
        return (
          <td key={harness}>
            {entry && (
              <details className="cell-detail">
                <summary>
                  <StateChip state={String(entry.state)} />
                </summary>
                <small>{`${String(entry.route) || "no route"} · ${fmtCount(num(entry.own))} own rows · ${fmtCount(num(entry.foreign))} rows on other routes (${fmtCount(num(entry.unexplained))} unexplained)`}</small>
              </details>
            )}
          </td>
        );
      })}
    </tr>
  );
}

/** Every known harness for All, so harnesses this machine does not use show as not in use. */
function CoverageGrid({ rows }: { rows: Row[] }) {
  const { filters } = useView();
  const harnesses: readonly Harness[] =
    filters.harness === "" ? HARNESSES : [filters.harness];
  const signals = [...new Map(rows.map((row) => [row.signal, row])).values()];
  const views = [...new Set(signals.map((row) => String(row.view)))];
  const cell: CoverageCell = (signal, harness) =>
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
                <CoverageRow
                  key={String(row.signal)}
                  row={row}
                  harnesses={harnesses}
                  cell={cell}
                />
              ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

/**
 * The coverage answer in numbers, from the server: cells, healthy cells, the count
 * of each other state, and per view the healthy and unexplained cells. Shown on
 * Pipeline (linked to the grid) and again at the top of the grid page.
 */
export function CoverageSummary(props: {
  data: Record<string, Row[]>;
  span: 4 | 12;
  linked: boolean;
  /** Lets the band see this panel's signals, like a Panel placed directly. */
  signals?: string[];
}) {
  const { data, span, linked } = props;
  const cells = num(data.coverage_summary?.[0]?.cells);
  const states = data.coverage_states ?? [];
  const healthy = num(states.find((row) => row.state === "healthy")?.cells);
  const others = states.filter(
    (row) => row.state !== "healthy" && num(row.cells) > 0,
  );
  return (
    <Panel
      title="Coverage by view"
      subtitle="Signal × harness cells for the selected window"
      signals={["pipeline.coverage"]}
      span={span}
    >
      <p className="coverage-total">
        <strong>{fmtCount(healthy)}</strong> healthy of {fmtCount(cells)} signal
        × harness cells
      </p>
      {others.length > 0 && (
        <ul className="coverage-states" aria-label="Cells by state">
          {others.map((row) => (
            <li key={String(row.state)}>
              <StateChip state={String(row.state)} />
              <span>{fmtCount(num(row.cells))}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="coverage-views">
        <DataTable
          columns={[
            {
              key: "view_title",
              label: "View",
              render: (row) =>
                linked ? (
                  <ViewLink view="pipeline/coverage">
                    {String(row.view_title)}
                  </ViewLink>
                ) : (
                  String(row.view_title)
                ),
            },
            {
              key: "healthy",
              label: "Healthy",
              numeric: true,
              render: (row) =>
                `${fmtCount(num(row.healthy))} / ${fmtCount(num(row.cells))}`,
            },
            { key: "unexplained", label: "Unexplained", numeric: true },
          ]}
          rows={data.coverage_by_view ?? []}
          label="Coverage by view"
        />
      </div>
    </Panel>
  );
}

/** L3: opens on the coverage numbers, then what each state means, then every cell. */
export default function PipelineCoverage({
  data,
}: {
  data: Record<string, Row[]>;
}) {
  return (
    <>
      <Section title="Coverage summary">
        <CoverageSummary data={data} span={12} linked={false} />
      </Section>
      <Section title="Every cell">
        <Panel
          title="Coverage grid"
          subtitle="Registry versus data for the selected window; open a cell for its counts"
          signals={["pipeline.coverage"]}
          span={12}
        >
          <ul className="state-legend" aria-label="Coverage states">
            {(data.coverage_states ?? []).map((row) => (
              <li key={String(row.state)}>
                <StateChip state={String(row.state)} />
                <small>{String(row.definition)}</small>
              </li>
            ))}
          </ul>
          <CoverageGrid rows={data.coverage ?? []} />
          <ViewLink view="pipeline">Open Pipeline</ViewLink>
        </Panel>
      </Section>
    </>
  );
}
