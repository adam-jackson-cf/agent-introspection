import {
  Children,
  Component,
  createContext,
  Fragment,
  isValidElement,
  useContext,
  useId,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import {
  type Contribution,
  type Contributions,
  type Filters,
  type Harness,
  type Registry,
  type SignalSupport,
  type ViewPath,
} from "./contracts";
import { fmtCount, fmtPct, HARNESS_COLOR, HARNESS_LABEL, num } from "./format";
import { PanelContext, type Column, type Series } from "./charts";
import { enabledHarnesses, isHidden } from "./harness-scope";

export type ViewContextValue = {
  registry: Registry;
  /** Server-owned threshold: rates over fewer samples are muted. */
  minRateN: number;
  contributions: Contributions;
  filters: Filters;
  sessionHref: (harness: string, session: string) => string;
  viewHref: (id: ViewPath) => string;
  navigate: (path: string, next: Filters) => void;
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

type Chips = (readonly [Harness, Contribution])[];

/** One chip per selected harness; none when no signal is harness-scoped. */
const panelChips = (context: ViewContextValue, signals: string[]): Chips => {
  const scoped = signals.filter((signal) =>
    context.registry.signals.some(
      (entry) => entry.signal === signal && entry.scope === "harness",
    ),
  );
  return scoped.length === 0
    ? []
    : selectedHarnesses(context).map(
        (harness) =>
          [
            harness,
            panelContribution(context.contributions, scoped, harness),
          ] as const,
      );
};

/** Chips agree when every harness has the same state and route start day. */
const sameChips = (a: Chips, b: Chips) =>
  a.length === b.length &&
  a.every(
    ([harness, chip], index) =>
      b[index]![0] === harness &&
      b[index]![1].state === chip.state &&
      b[index]![1].from === chip.from,
  );

/** The chips of the enclosing Section band; null outside one. */
const BandContext = createContext<Chips | null>(null);

/**
 * Which harnesses contributed rows to the signals in the window. Row counts
 * and route start days sit in the panel's `i` popover.
 */
export function Contributors({ signals }: { signals: string[] }) {
  const context = useView();
  const chips = panelChips(context, signals);
  if (chips.length === 0) return null;
  return (
    <ul className="contributors" aria-label="Harnesses contributing data">
      {chips.map(([harness, chip]) => (
        <li
          key={harness}
          className={`contributor contributor-${chip.state.replaceAll(" ", "-")}`}
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
      ))}
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
      {panelChips(context, signals).length > 0 && (
        <ul className="chip-details" aria-label="Harnesses contributing data">
          {panelChips(context, signals).map(([harness, chip]) => (
            <li key={harness}>{chipTitle(harness, chip)}</li>
          ))}
        </ul>
      )}
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

/** The "i" button: hover, focus or click opens its note; Escape closes it. */
export function InfoPopover({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const noteId = useId();
  return (
    <span
      className="panel-info"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") setOpen(false);
      }}
    >
      <button
        className="details"
        aria-describedby={noteId}
        aria-label={label}
        onClick={() => setOpen(true)}
      >
        <span aria-hidden="true">i</span>
      </button>
      <div id={noteId} role="tooltip" className="panel-popover" hidden={!open}>
        {children}
      </div>
    </span>
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
  id,
  children,
}: {
  title: string;
  subtitle?: string;
  signals: string[];
  span?: 1 | 3 | 4 | 5 | 6 | 7 | 8 | 12;
  /** Anchor for in-page links such as a KPI's "Show …" link. */
  id?: string;
  children: ReactNode;
}) {
  const context = useView();
  const band = useContext(BandContext);
  const shown = signals.filter((signal) => !isHidden(context.registry, signal));
  const missing = notApplicable(context, shown);
  if (signals[0] !== undefined && shown[0] !== signals[0]) return null;
  const chips = panelChips(context, shown);
  return (
    <article className="panel" data-span={span} id={id}>
      <header className="panel-head">
        <div>
          <h3>{title}</h3>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <InfoPopover label={`Definition of ${title}`}>
          <InfoNote signals={shown} />
        </InfoPopover>
      </header>
      {chips.length > 0 && (!band || !sameChips(chips, band)) && (
        <Contributors signals={shown} />
      )}
      <div className="panel-body">
        <PanelContext.Provider
          value={{ label: title, harnessChips: chips.length > 0 }}
        >
          <Boundary label={title}>
            {missing ? <NotApplicableNotice entries={missing} /> : children}
          </Boundary>
        </PanelContext.Provider>
      </div>
    </article>
  );
}

/** One headline number; the value is null when nothing contributes. */
export function Kpi({
  title,
  value,
  detail,
  signals,
  n,
  span = 3,
  children,
}: {
  title: string;
  value: string;
  detail?: string;
  signals: string[];
  /** Sample size of a rate value: muted when below the server's threshold. */
  n?: number;
  /** 1 is one column of a five-column band (`Section columns={5}`). */
  span?: 1 | 3;
  /** A link to the detail behind the figure. */
  children?: ReactNode;
}) {
  const { minRateN } = useView();
  const muted = n !== undefined && isMutedRate(n, minRateN);
  return (
    <Panel title={title} signals={signals} span={span}>
      <div className="kpi">
        <strong className={muted ? "muted-rate" : undefined}>{value}</strong>
        {detail && <span>{detail}</span>}
        {children}
      </div>
    </Panel>
  );
}

/** Each panel's signal list directly inside a band, skipping hidden ones. */
const bandSignals = (
  children: ReactNode,
  hidden: (s: string) => boolean,
): string[][] =>
  Children.toArray(children).flatMap((child): string[][] => {
    if (!isValidElement<{ signals?: string[]; children?: ReactNode }>(child))
      return [];
    if (child.type === Fragment)
      return bandSignals(child.props.children, hidden);
    const signals = child.props.signals;
    return Array.isArray(signals) && !(signals[0] && hidden(signals[0]))
      ? [signals.filter((signal) => !hidden(signal))]
      : [];
  });

/**
 * A band of panels. Its heading is followed by one contributor row for the
 * union of its panels' signals when at least one panel shares that row; a
 * panel repeats a row only when its own chips differ from the band's.
 */
export function Section({
  title,
  columns,
  children,
}: {
  title: string;
  /** Five lets a band of five KPIs share one row; the default is twelve. */
  columns?: 5;
  children: ReactNode;
}) {
  const context = useView();
  const panels = bandSignals(children, (signal) =>
    isHidden(context.registry, signal),
  );
  const union = [...new Set(panels.flat())];
  const chips = panelChips(context, union);
  const shared =
    chips.length > 0 &&
    panels.some((signals) => sameChips(panelChips(context, signals), chips));
  return (
    <section className="view-section">
      <h2>{title}</h2>
      {shared && <Contributors signals={union} />}
      <BandContext.Provider value={shared ? chips : null}>
        <div className="grid" data-columns={columns}>
          {children}
        </div>
      </BandContext.Provider>
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

export const isPlainClick = (event: MouseEvent) =>
  event.button === 0 &&
  !event.metaKey &&
  !event.ctrlKey &&
  !event.shiftKey &&
  !event.altKey;

/** A link to another view that keeps the current range and harness, like the nav. */
export function ViewLink({
  view,
  children,
}: {
  view: ViewPath;
  children: ReactNode;
}) {
  const { viewHref, navigate, filters } = useView();
  const href = viewHref(view);
  return (
    <a
      className="view-link"
      href={href}
      onClick={(event) => {
        if (!isPlainClick(event)) return;
        event.preventDefault();
        navigate(href, filters);
      }}
    >
      {children}
    </a>
  );
}

/** An in-page link to a panel on this page; it scrolls without touching the URL or the data. */
export function ShowLink(props: { target: string; children: string }) {
  return (
    <a
      className="view-link"
      href={`#${props.target}`}
      onClick={(event) => {
        if (!isPlainClick(event)) return;
        event.preventDefault();
        document.getElementById(props.target)?.scrollIntoView();
      }}
    >
      {props.children}
    </a>
  );
}

/** A rate over fewer samples than the server's threshold is muted, not hidden. */
export const isMutedRate = (n: number, minRateN: number) => n < minRateN;

/** A formatted rate with its sample size; dimmed when n is below the server's threshold. */
export function RateValue(props: {
  pct: number | null;
  n: number;
  digits?: number;
}) {
  const { minRateN } = useView();
  return (
    <span className={isMutedRate(props.n, minRateN) ? "muted-rate" : undefined}>
      {fmtPct(props.pct, props.digits)}
      <span className="rate-n"> · n={fmtCount(props.n)}</span>
    </span>
  );
}

/**
 * Table columns for a pivoted daily rate: each series' cell is the rate and
 * reads its sample size from the row's `<series>_n` (the daily query's denominator).
 */
export const rateSeriesColumns = (series: Series[]): Column[] =>
  series.map((entry) => ({
    key: entry.key,
    label: `${entry.label} %`,
    numeric: true,
    render: (row) => (
      <RateValue
        pct={typeof row[entry.key] === "number" ? Number(row[entry.key]) : null}
        n={num(row[`${entry.key}_n`])}
        digits={1}
      />
    ),
  }));
