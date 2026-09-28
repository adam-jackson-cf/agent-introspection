import {
  Component,
  createContext,
  useContext,
  useId,
  useState,
  type ReactNode,
} from "react";
import {
  HARNESSES,
  type Contributions,
  type Filters,
  type Harness,
  type Registry,
  type SignalSupport,
} from "./contracts";
import { fmtCount, HARNESS_COLOR, HARNESS_LABEL } from "./format";

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

const selectedHarnesses = (filters: Filters): readonly Harness[] =>
  filters.harness === "" ? HARNESSES : [filters.harness];

const supportFor = (registry: Registry, signal: string, harness: Harness) =>
  registry.support.find(
    (entry) => entry.signal === signal && entry.harness === harness,
  );

/** True when every listed signal is not emitted by every selected harness. */
export function notEmitted(
  context: ViewContextValue,
  signals: string[],
): SignalSupport[] | null {
  const entries = selectedHarnesses(context.filters).flatMap((harness) =>
    signals.map((signal) => supportFor(context.registry, signal, harness)),
  );
  return entries.length > 0 &&
    entries.every((entry) => entry?.alignment === "not emitted")
    ? (entries as SignalSupport[])
    : null;
}

type ContributionState = "rows" | "no rows" | "not emitted";

function contributionState(
  context: ViewContextValue,
  signals: string[],
  harness: Harness,
): { state: ContributionState; rows: number } {
  const emitted = signals.filter(
    (signal) =>
      supportFor(context.registry, signal, harness)?.alignment !==
      "not emitted",
  );
  if (emitted.length === 0) return { state: "not emitted", rows: 0 };
  const rows = Math.max(
    0,
    ...emitted.map((signal) => context.contributions[signal]?.[harness] ?? 0),
  );
  return { state: rows > 0 ? "rows" : "no rows", rows };
}

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
      {selectedHarnesses(context.filters).map((harness) => {
        const { state, rows } = contributionState(
          context,
          harnessSignals,
          harness,
        );
        return (
          <li
            key={harness}
            className={`contributor ${state.replace(" ", "-")}`}
            title={
              state === "rows"
                ? `${HARNESS_LABEL[harness]}: ${fmtCount(rows)} rows on its route`
                : `${HARNESS_LABEL[harness]}: ${state}`
            }
          >
            <i
              style={{
                background:
                  state === "rows" ? HARNESS_COLOR[harness] : undefined,
              }}
              aria-hidden="true"
            />
            {HARNESS_LABEL[harness]}
            {state !== "rows" && <small>{state}</small>}
          </li>
        );
      })}
    </ul>
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
                  {selectedHarnesses(context.filters).map((harness) => {
                    const support = supportFor(
                      context.registry,
                      signal,
                      harness,
                    );
                    if (!support) return null;
                    const route = context.registry.routes.find(
                      (entry) =>
                        entry.route === support.route &&
                        entry.harness === harness,
                    );
                    const rows = context.contributions[signal]?.[harness];
                    return (
                      <tr key={harness}>
                        <th scope="row">{HARNESS_LABEL[harness]}</th>
                        <td>
                          <span
                            className={`alignment ${support.alignment.replace(" ", "-")}`}
                          >
                            {support.alignment}
                          </span>
                        </td>
                        <td>
                          {route?.description ?? ""}
                          {support.note && <em>{support.note}</em>}
                          {support.alignment !== "not emitted" && (
                            <small>
                              {fmtCount(rows ?? 0)} rows on this route in the
                              window
                            </small>
                          )}
                        </td>
                      </tr>
                    );
                  })}
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

export function NotEmittedNotice({ entries }: { entries: SignalSupport[] }) {
  const notes = [...new Set(entries.map((entry) => entry.note))];
  return (
    <div className="not-emitted" role="status">
      <b>Not emitted</b>
      {notes.map((note) => (
        <p key={note}>{note}</p>
      ))}
    </div>
  );
}

/**
 * Panel frame shared by every view: title, contributors, and an info note
 * rendered from the signal support registry. When every selected harness does
 * not emit the panel's signals, the body is replaced by the registry's reason,
 * never by zero.
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
  const missing = notEmitted(context, signals);
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
      <Contributors signals={signals} />
      <div className="panel-body">
        <Boundary label={title}>
          {missing ? <NotEmittedNotice entries={missing} /> : children}
        </Boundary>
      </div>
      {open && (
        <footer id={noteId} className="panel-footer">
          <InfoNote signals={signals} />
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

export function HarnessSelector({
  value,
  onChange,
}: {
  value: Filters["harness"];
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
        {HARNESSES.map((harness) => (
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
