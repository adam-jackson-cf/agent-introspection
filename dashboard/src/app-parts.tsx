import type { MouseEvent } from "react";
import type {
  Filters,
  Registry,
  SessionResponse,
  ViewId,
  ViewResponse,
} from "./contracts";
import { HarnessSelector, ViewContext } from "./components";
import { PRESETS, fromInput, presetFor, toInput } from "./app-state";
import { VIEWS, type Location } from "./routes";
import Session from "./views/Session";

export type Navigate = (path: string, next: Filters) => void;

const isPlainClick = (event: MouseEvent) =>
  event.button === 0 &&
  !event.metaKey &&
  !event.ctrlKey &&
  !event.shiftKey &&
  !event.altKey;

export function ViewNav(props: {
  location: Location;
  filters: Filters;
  viewHref: (id: ViewId) => string;
  navigate: Navigate;
}) {
  const { location, filters, viewHref, navigate } = props;
  const current = location.kind === "view" ? location.view.id : null;
  return VIEWS.map((view) => (
    <a
      key={view.id}
      className={`nav${current === view.id ? " active" : ""}`}
      href={viewHref(view.id)}
      aria-current={current === view.id ? "page" : undefined}
      onClick={(event) => {
        if (!isPlainClick(event)) return;
        event.preventDefault();
        navigate(viewHref(view.id), filters);
      }}
    >
      <span className="nav-num">{view.number}</span>
      <span>{view.title}</span>
    </a>
  ));
}

export function Sidebar({ nav }: { nav: React.ReactNode }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="mark" />
        Agent <span>introspection</span>
      </div>
      <nav aria-label="Views">{nav}</nav>
      <p className="note">
        One definition per signal for every harness. Harnesses are never ranked;
        each panel's notes say how each producer reaches the signal.
      </p>
    </aside>
  );
}

export function Topbar(props: {
  title: string;
  nav: React.ReactNode;
  onRefresh: () => void;
}) {
  return (
    <>
      <header className="topbar">
        <div className="crumb">
          Views / <b>{props.title}</b>
        </div>
        <button className="refresh" onClick={props.onRefresh}>
          ↻ Refresh
        </button>
      </header>
      <nav className="mobile-nav" aria-label="Views">
        {props.nav}
      </nav>
    </>
  );
}

const headingQuestion = (location: Location) => {
  if (location.kind === "view") return location.view.question;
  if (location.kind === "session")
    return "Tasks, usage, tool calls, and user signals for one session.";
  return `${window.location.pathname} is not a view.`;
};

const liveText = (
  loading: boolean,
  failure: string,
  viewResponse: ViewResponse | null,
  sessionResponse: SessionResponse | null,
) => {
  if (loading) return "Loading…";
  if (failure) return "Query failed";
  if (viewResponse)
    return `Queried ${viewResponse.queriedAt.slice(11, 19)} UTC · ${viewResponse.elapsedMs} ms`;
  if (sessionResponse)
    return `Queried ${sessionResponse.queriedAt.slice(11, 19)} UTC`;
  return "";
};

export function PageHead(props: {
  location: Location;
  title: string;
  heading: React.RefObject<HTMLHeadingElement | null>;
  loading: boolean;
  failure: string;
  viewResponse: ViewResponse | null;
  sessionResponse: SessionResponse | null;
}) {
  const { location, title, heading, loading, failure } = props;
  return (
    <div className="head">
      <div>
        <div className="eyebrow">
          {location.kind === "view" ? location.view.number : "Drill-down"}
        </div>
        <h1 ref={heading} tabIndex={-1}>
          {title}
        </h1>
        <p>{headingQuestion(location)}</p>
      </div>
      <div className="live" aria-live="polite">
        {liveText(loading, failure, props.viewResponse, props.sessionResponse)}
      </div>
    </div>
  );
}

function CustomRange(props: {
  filters: Filters;
  change: (next: Filters) => void;
}) {
  const { filters, change } = props;
  return (
    <div className="custom-range">
      <input
        type="datetime-local"
        aria-label="Start (UTC)"
        value={toInput(filters.start)}
        onChange={(event) =>
          change({
            ...filters,
            start: fromInput(event.target.value, filters.start),
          })
        }
      />
      <input
        type="datetime-local"
        aria-label="End (UTC)"
        value={toInput(filters.end)}
        onChange={(event) =>
          change({
            ...filters,
            end: fromInput(event.target.value, filters.end),
          })
        }
      />
    </div>
  );
}

export function FilterBar(props: {
  filters: Filters;
  custom: boolean;
  setCustom: (custom: boolean) => void;
  setRange: (days: number) => void;
  change: (next: Filters) => void;
}) {
  const { filters, custom, setCustom, setRange, change } = props;
  return (
    <div className="filters" aria-label="Filters">
      <div className="filter">
        <label htmlFor="range">Time range · UTC</label>
        <select
          id="range"
          value={custom ? "Custom range" : presetFor(filters)}
          onChange={(event) => {
            const preset = PRESETS[event.target.value];
            setCustom(preset === undefined);
            if (preset !== undefined) setRange(preset);
          }}
        >
          {[...Object.keys(PRESETS), "Custom range"].map((label) => (
            <option key={label}>{label}</option>
          ))}
        </select>
        {custom && <CustomRange filters={filters} change={change} />}
      </div>
      <HarnessSelector
        value={filters.harness}
        onChange={(harness) => change({ ...filters, harness })}
      />
      <p className="scope">
        {filters.harness === ""
          ? "All = the union of each harness's rows for the same signal"
          : "One harness: signals not applicable to it say so, never zero"}
      </p>
    </div>
  );
}

export function Notices(props: { location: Location; failure: string }) {
  return (
    <>
      {props.failure && (
        <div className="state-notice" role="alert">
          <b>Query failed — outcome unknown</b>
          <span>{props.failure}</span>
        </div>
      )}
      {props.location.kind === "unknown" && (
        <div className="state-notice" role="alert">
          <b>Unknown view</b>
          <span>Select a view from the navigation.</span>
        </div>
      )}
    </>
  );
}

export function PageBody(props: {
  location: Location;
  registry: Registry | null;
  filters: Filters;
  sessionHref: (harness: string, session: string) => string;
  viewResponse: ViewResponse | null;
  sessionResponse: SessionResponse | null;
}) {
  const { location, registry, filters, sessionHref } = props;
  const { viewResponse, sessionResponse } = props;
  if (!registry) return null;
  if (location.kind === "view" && viewResponse)
    return (
      <ViewContext.Provider
        value={{
          registry,
          contributions: viewResponse.contributions,
          filters,
          sessionHref,
        }}
      >
        <location.view.component data={viewResponse.data} />
      </ViewContext.Provider>
    );
  if (location.kind === "session" && sessionResponse)
    return (
      <ViewContext.Provider
        value={{ registry, contributions: {}, filters, sessionHref }}
      >
        <Session response={sessionResponse} />
      </ViewContext.Provider>
    );
  return null;
}
