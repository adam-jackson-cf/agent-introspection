import {
  Component,
  createContext,
  useContext,
  useId,
  useState,
  type ReactNode,
} from "react";
import {
  type Contribution,
  type Contributions,
  type Filters,
  type Harness,
  type Registry,
  type SignalSupport,
} from "./contracts";
import { fmtCount, HARNESS_COLOR, HARNESS_LABEL } from "./format";
import { enabledHarnesses, isHidden } from "./harness-scope";

export type ViewContextValue = {
  registry: Registry;
  contributions: Contributions;
  filters: Filters;
  sessionHref: (harness: string, session: string) => string;
};
export const ViewContext = createContext<ViewContextValue | null>(null);
export const useView = (): ViewContextValue => {
  const value = useContext(ViewContext);
  if (!value) throw new Error("view context is missing");
  return value;
};

/** The selected harness, or every enabled harness for All. */
const selectedHarnesses = (
  context: Pick<ViewContextValue, "registry" | "filters">,
): readonly Harness[] =>
  context.filters.harness === ""
    ? enabledHarnesses(context.registry)
    : [context.filters.harness];

/** The harnesses a view's charts and tables cover: the selection, or All = enabled. */
export const useSelectedHarnesses = (): readonly Harness[] =>
  selectedHarnesses(useView());

/** Whether a signal is shown on this machine (every enabled harness reaches it). */
export const useVisible = (signal: string): boolean =>
  !isHidden(useView().registry, signal);

const supportFor = (registry: Registry, signal: string, harness: Harness) =>
  registry.support.find(
    (entry) => entry.signal === signal && entry.harness === harness,
  );

/**
 * The panel's measured signal is listed first; the rest only describe it (for
 * example the effort dimension). Returns the registry entries when the measured
 * signal is not applicable to any selected harness.
 */
export function notApplicable(
  context: ViewContextValue,
  signals: string[],
): SignalSupport[] | null {
  const measured = signals[0];
  if (!measured) return null;
  const entries = selectedHarnesses(context).map((harness) =>
    supportFor(context.registry, measured, harness),
  );
  return entries.length > 0 &&
    entries.every((entry) => entry?.alignment === "not applicable")
    ? (entries as SignalSupport[])
    : null;
}

/** Most telling first when no signal of the panel has rows. */
const SILENT_ORDER: Contribution["state"][] = [
  "route missing",
  "no events",
  "no activity",
  "not applicable",
];

/**
 * One harness's chip for a panel: rows when any of its signals has rows on the
 * harness's route (with the measured signal's start day when that falls in the
 * window), else the most telling coverage state among its signals.
 */
export function panelContribution(
  contributions: Contributions,
  signals: string[],
  harness: Harness,
): Contribution {
  const entries = signals
    .map((signal) => contributions[signal]?.[harness])
    .filter((entry): entry is Contribution => entry !== undefined);
  const withRows = entries.filter((entry) => entry.state === "rows");
  if (withRows.length > 0)
    return {
      state: "rows",
      rows: Math.max(...withRows.map((entry) => entry.rows)),
      from: withRows[0]!.from,
    };
  const state =
    SILENT_ORDER.find((candidate) =>
      entries.some((entry) => entry.state === candidate),
    ) ?? "not applicable";
  return { state, rows: 0, from: null };
}

const chipTitle = (harness: Harness, chip: Contribution) => {
  const label = HARNESS_LABEL[harness];
  if (chip.state !== "rows") return `${label}: ${chip.state}`;
  const since = chip.from ? `; its route starts on ${chip.from}` : "";
  return `${label}: ${fmtCount(chip.rows)} rows on its route${since}`;
};

/** Which harnesses contributed rows to the panel's signals in the window. */
export function Contributors({ signals }: { signals: string[] }) {
  const context = useView();
  const harnessSignals = signals.filter((signal) =>
    context.registry.signals.some(
      (entry) => entry.signal === signal && entry.scope === "harness",
    ),
  );
  if (harnessSignals.length === 0) return null;
  return (
    <ul className="contributors" aria-label="Harnesses contributing data">
      {selectedHarnesses(context).map((harness) => {
        const chip = panelContribution(
          context.contributions,
          harnessSignals,
          harness,
        );
        return (
          <li
            key={harness}
            className={`contributor contributor-${chip.state.replaceAll(" ", "-")}`}
            title={chipTitle(harness, chip)}
          >
            <i
              style={{
                background:
                  chip.state === "rows" ? HARNESS_COLOR[harness] : undefined,
              }}
              aria-hidden="true"
            />
            {HARNESS_LABEL[harness]}
            {chip.state !== "rows" && <small>{chip.state}</small>}
            {chip.state === "rows" && chip.from && (
              <small>from {chip.from}</small>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** One harness's route and alignment for a signal; nothing when unsupported. */
function SupportRow(props: {
  context: ViewContextValue;
  signal: string;
  harness: Harness;
}) {
  const { context, signal, harness } = props;
  const support = supportFor(context.registry, signal, harness);
  if (!support) return null;
  const route = context.registry.routes.find(
    (entry) => entry.route === support.route && entry.harness === harness,
  );
  const rows = context.contributions[signal]?.[harness]?.rows;
  return (
    <tr>
      <th scope="row">{HARNESS_LABEL[harness]}</th>
      <td>
        <span className={`alignment ${support.alignment.replaceAll(" ", "-")}`}>
          {support.alignment}
        </span>
      </td>
      <td>
        {route?.description ?? ""}
        {support.note && <em>{support.note}</em>}
        {support.alignment !== "not applicable" && (
          <small>{fmtCount(rows ?? 0)} rows on this route in the window</small>
        )}
      </td>
    </tr>
  );
}

/** Registry-driven note: definition, then each harness's route and alignment. */
export function InfoNote({ signals }: { signals: string[] }) {
  const context = useView();
  return (
    <div className="info-note">
      {signals.map((signal) => {
        const definition = context.registry.signals.find(
          (entry) => entry.signal === signal,
        );
        if (!definition) return null;
        return (
          <section key={signal}>
            <b>{definition.title}</b>
            <p>{definition.question}</p>
            <p className="formula">{definition.formula}</p>
            {definition.scope === "harness" && (
              <table>
                <tbody>
                  {selectedHarnesses(context).map((harness) => (
                    <SupportRow
                      key={harness}
                      context={context}
                      signal={signal}
                      harness={harness}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </section>
        );
      })}
    </div>
  );
}

class Boundary extends Component<
  { children: ReactNode; label: string },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="state-notice" role="alert">
        <b>{this.props.label} could not be displayed.</b>
      </div>
    ) : (
      this.props.children
    );
  }
}

export function NotApplicableNotice({ entries }: { entries: SignalSupport[] }) {
  const notes = [...new Set(entries.map((entry) => entry.note))];
  return (
    <div className="not-applicable" role="status">
      <b>Not applicable</b>
      {notes.map((note) => (
        <p key={note}>{note}</p>
      ))}
    </div>
  );
}

/**
 * Panel frame shared by every view: title, contributors, and an info note
 * rendered from the signal support registry. When the panel's signal is not
 * applicable to any selected harness, the body is replaced by the registry's
 * reason, never by zero. A panel whose measured signal (the first) is hidden on
 * this machine is not rendered; other hidden signals drop out of its chips and note.
 */
export function Panel({
  title,
  subtitle,
  signals,
  span = 6,
  children,
}: {
  title: string;
  subtitle?: string;
  signals: string[];
  span?: 3 | 4 | 5 | 6 | 7 | 8 | 12;
  children: ReactNode;
}) {
  const context = useView();
  const [open, setOpen] = useState(false);
  const noteId = useId();
  const shown = signals.filter((signal) => !isHidden(context.registry, signal));
  const missing = notApplicable(context, shown);
  if (signals[0] !== undefined && shown[0] !== signals[0]) return null;
  return (
    <article className="panel" data-span={span}>
      <header className="panel-head">
        <div>
          <h3>{title}</h3>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <button
          className="details"
          aria-expanded={open}
          aria-controls={noteId}
          aria-label={
            open ? `Hide notes for ${title}` : `Show notes for ${title}`
          }
          onClick={() => setOpen((value) => !value)}
        >
          <span aria-hidden="true">i</span>
        </button>
      </header>
      <Contributors signals={shown} />
      <div className="panel-body">
        <Boundary label={title}>
          {missing ? <NotApplicableNotice entries={missing} /> : children}
        </Boundary>
      </div>
      {open && (
        <footer id={noteId} className="panel-footer">
          <InfoNote signals={shown} />
        </footer>
      )}
    </article>
  );
}

/** One headline number; the value is null when nothing contributes. */
export function Kpi({
  title,
  value,
  detail,
  signals,
}: {
  title: string;
  value: string;
  detail?: string;
  signals: string[];
}) {
  return (
    <Panel title={title} signals={signals} span={3}>
      <div className="kpi">
        <strong>{value}</strong>
        {detail && <span>{detail}</span>}
      </div>
    </Panel>
  );
}

export function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="view-section">
      <h2>{title}</h2>
      <div className="grid">{children}</div>
    </section>
  );
}

/** All plus each harness this machine uses. */
export function HarnessSelector({
  value,
  harnesses,
  onChange,
}: {
  value: Filters["harness"];
  harnesses: readonly Harness[];
  onChange: (value: Filters["harness"]) => void;
}) {
  return (
    <div className="filter">
      <label htmlFor="harness">Harness</label>
      <select
        id="harness"
        value={value}
        onChange={(event) => onChange(event.target.value as Filters["harness"])}
      >
        <option value="">All harnesses</option>
        {harnesses.map((harness) => (
          <option key={harness} value={harness}>
            {HARNESS_LABEL[harness]}
          </option>
        ))}
      </select>
    </div>
  );
}

export function HarnessName({ value }: { value: unknown }) {
  const harness = String(value) as Harness;
  return (
    <span className="harness-name">
      <i style={{ background: HARNESS_COLOR[harness] }} aria-hidden="true" />
      {HARNESS_LABEL[harness] ?? String(value)}
    </span>
  );
}

export function SessionLink({
  harness,
  session,
  children,
}: {
  harness: unknown;
  session: unknown;
  children?: ReactNode;
}) {
  const { sessionHref } = useView();
  const id = String(session ?? "");
  if (id === "") return <span>—</span>;
  return (
    <a className="session-link" href={sessionHref(String(harness), id)}>
      {children ?? `${id.slice(0, 8)}…`}
    </a>
  );
}
