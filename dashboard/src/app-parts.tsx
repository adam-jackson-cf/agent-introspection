import type {
  Filters,
  Harness,
  Registry,
  SessionResponse,
  ViewPath,
  ViewResponse,
} from "./contracts";
import {
  HarnessSelector,
  InfoPopover,
  ViewContext,
  isPlainClick,
} from "./components";
import { PRESETS, fromInput, presetFor, toInput } from "./app-state";
import { VIEWS, type Location } from "./routes";
import Session from "./views/Session";

export type Navigate = (path: string, next: Filters) => void;

export function ViewNav(props: {
  location: Location;
  filters: Filters;
  viewHref: (id: ViewPath) => string;
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
  /** The parent view of a detail page, shown as a link in the breadcrumb. */
  parent?: { title: string; href: string; onOpen: () => void };
  nav: React.ReactNode;
  onRefresh: () => void;
}) {
  const { parent } = props;
  return (
    <>
      <header className="topbar">
        <div className="crumb">
          Views /{" "}
          {parent && (
            <>
              <a
                href={parent.href}
                onClick={(event) => {
                  if (!isPlainClick(event)) return;
                  event.preventDefault();
                  parent.onOpen();
                }}
              >
                {parent.title}
              </a>{" "}
              /{" "}
            </>
          )}
          <b>{props.title}</b>
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
  if (location.kind === "view")
    return location.detail?.question ?? location.view.question;
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

/** Where the query time went, and how fresh the facts are; both come from the server. */
function ViewDetail({ response }: { response: ViewResponse }) {
  const { timings, asOf } = response;
  return (
    <>
      <span className="timings">
        {" "}
        (registry {timings.registryMs} · route counts {timings.routeCountsMs} ·
        SQL {timings.sqlMs} ms)
      </span>
      {asOf && (
        <div className={asOf.stale ? "as-of-stale" : undefined}>
          Data as of {asOf.at.slice(11, 16)} UTC
          {asOf.stale ? " · older than the refresh schedule" : ""}
          {asOf.historyStale ? " · older history not refreshed" : ""}
        </div>
      )}
    </>
  );
}

export function PageHead(props: {
  location: Location;
  title: string;
  heading: React.RefObject<HTMLHeadingElement | null>;
  loading: boolean;
  elapsedSeconds: number;
  failure: string;
  viewResponse: ViewResponse | null;
  sessionResponse: SessionResponse | null;
}) {
  const { location, title, heading, loading, failure, viewResponse } = props;
  return (
    <div className="head">
      <div>
        <div className="eyebrow">
          {location.kind === "view" && !location.detail
            ? location.view.number
            : "Drill-down"}
        </div>
        <h1 ref={heading} tabIndex={-1}>
          {title}
        </h1>
        <p>{headingQuestion(location)}</p>
      </div>
      <div className="live">
        <span aria-live="polite">
          {liveText(loading, failure, viewResponse, props.sessionResponse)}
        </span>
        {loading && props.elapsedSeconds > 0 && (
          <span aria-hidden="true"> {props.elapsedSeconds} s</span>
        )}
        {!loading && !failure && viewResponse && (
          <ViewDetail response={viewResponse} />
        )}
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
            range: undefined,
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
            range: undefined,
          })
        }
      />
    </div>
  );
}

export function FilterBar(props: {
  filters: Filters;
  harnesses: readonly Harness[];
  custom: boolean;
  setCustom: (custom: boolean) => void;
  setRange: (days: number) => void;
  change: (next: Filters) => void;
}) {
  const { filters, harnesses, custom, setCustom, setRange, change } = props;
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
        {(custom || presetFor(filters) === "Custom range") && (
          <CustomRange filters={filters} change={change} />
        )}
      </div>
      <div className="filter-group">
        <HarnessSelector
          value={filters.harness}
          harnesses={harnesses}
          onChange={(harness) => change({ ...filters, harness })}
        />
        <InfoPopover label="What the harness choice covers">
          <p className="scope">
            {filters.harness === ""
              ? "All = the union of each enabled harness's rows for the same signal"
              : "One harness: signals not applicable to it say so, never zero"}
          </p>
        </InfoPopover>
      </div>
    </div>
  );
}

export function Notices(props: {
  location: Location;
  failure: string;
  onRetry: () => void;
}) {
  return (
    <>
      {props.failure && (
        <div className="state-notice" role="alert">
          <div className="state-notice-row">
            <div>
              <b>Query failed. Try a shorter range or retry.</b>
              <span>{props.failure}</span>
            </div>
            <button className="refresh" onClick={props.onRetry}>
              Retry
            </button>
          </div>
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

/** Said once above the bands when every dataset of the view is empty for the range. */
export function RangeNotice({ response }: { response: ViewResponse | null }) {
  if (
    !response ||
    !Object.values(response.data).every((rows) => rows.length === 0)
  )
    return null;
  return (
    <p className="range-notice">
      No data for this view in the selected range or harness.
    </p>
  );
}

/** Said once when some datasets failed: panels not tied to one (`source`) may be incomplete. */
export function PartialNotice({ response }: { response: ViewResponse | null }) {
  const failed = Object.keys(response?.errors ?? {});
  if (failed.length === 0) return null;
  return (
    <p className="range-notice" role="status">
      {failed.length} of this view&apos;s datasets could not be loaded (
      {failed.join(", ")}). Panels built from them show the failure; the rest is
      unaffected.
    </p>
  );
}

/** Page-shaped placeholder: the header stays, then a KPI strip, a chart band and a table band. */
export function LoadingSkeleton() {
  return (
    <div className="skeleton" role="status">
      <p className="skeleton-label">Loading the view…</p>
      <div className="skeleton-kpis">
        <i />
        <i />
        <i />
        <i />
      </div>
      <i className="skeleton-chart" />
      <i className="skeleton-table" />
    </div>
  );
}

export function PageBody(props: {
  location: Location;
  registry: Registry | null;
  filters: Filters;
  sessionHref: (harness: string, session: string) => string;
  viewHref: (id: ViewPath) => string;
  navigate: Navigate;
  viewResponse: ViewResponse | null;
  sessionResponse: SessionResponse | null;
}) {
  const { location, registry, filters, sessionHref } = props;
  const { viewHref, navigate } = props;
  const { viewResponse, sessionResponse } = props;
  if (!registry) return null;
  if (location.kind === "view" && viewResponse)
    return (
      <ViewContext.Provider
        value={{
          registry,
          contributions: viewResponse.contributions,
          errors: viewResponse.errors,
          filters,
          sessionHref,
          minRateN: viewResponse.thresholds.minRateN,
          viewHref,
          navigate,
        }}
      >
        {location.detail ? (
          <location.detail.component data={viewResponse.data} />
        ) : (
          <location.view.component data={viewResponse.data} />
        )}
      </ViewContext.Provider>
    );
  if (location.kind === "session" && sessionResponse)
    return (
      <ViewContext.Provider
        value={{
          registry,
          contributions: {},
          filters,
          sessionHref,
          minRateN: 0,
          viewHref,
          navigate,
          errors: {},
        }}
      >
        <Session response={sessionResponse} />
      </ViewContext.Provider>
    );
  return null;
}
