import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentType,
} from "react";
import {
  HARNESSES,
  type Filters,
  type Registry,
  type Row,
  type SessionResponse,
  type ViewId,
  type ViewResponse,
} from "./contracts";
import { HarnessSelector, ViewContext } from "./components";
import { isRecord } from "./type-guards";
import Cache from "./views/Cache";
import Effort from "./views/Effort";
import Friction from "./views/Friction";
import Guardrails from "./views/Guardrails";
import Interventions from "./views/Interventions";
import Pipeline from "./views/Pipeline";
import Provider from "./views/Provider";
import Recurrence from "./views/Recurrence";
import Session from "./views/Session";
import Tools from "./views/Tools";

type ViewSpec = {
  id: ViewId;
  number: string;
  title: string;
  question: string;
  component: ComponentType<{ data: Record<string, Row[]> }>;
};

/** Question-led views, in reading order. */
export const VIEWS: ViewSpec[] = [
  {
    id: "pipeline",
    number: "V1",
    title: "Pipeline",
    question:
      "Is the fact loader fresh, complete, and correct for every harness?",
    component: Pipeline,
  },
  {
    id: "cache",
    number: "V2",
    title: "Cache efficiency",
    question:
      "Where are tokens consumed, and how much input is served from cache?",
    component: Cache,
  },
  {
    id: "effort",
    number: "V3",
    title: "Reasoning effort",
    question: "Does heavier reasoning effort earn its cost?",
    component: Effort,
  },
  {
    id: "tools",
    number: "V4",
    title: "Tool failures",
    question:
      "Which tools fail, how often, in which tasks, and do they repeat or loop?",
    component: Tools,
  },
  {
    id: "friction",
    number: "V5",
    title: "Friction",
    question:
      "How often do users interrupt, steer, or follow up quickly, and do tasks finish cleanly?",
    component: Friction,
  },
  {
    id: "guardrails",
    number: "V6",
    title: "Guardrails",
    question:
      "How often do approvals, sandboxes, and quality gates block or get bypassed?",
    component: Guardrails,
  },
  {
    id: "provider",
    number: "V7",
    title: "Provider",
    question:
      "Are model calls failing, slow, disconnecting, retried, or served by a different model?",
    component: Provider,
  },
  {
    id: "recurrence",
    number: "V8",
    title: "Recurrence",
    question:
      "Which files and failures recur across tasks, and which cross the threshold for intervention?",
    component: Recurrence,
  },
  {
    id: "interventions",
    number: "V9",
    title: "Interventions",
    question:
      "Which findings became interventions, and did applied interventions reduce what they targeted?",
    component: Interventions,
  },
];

const PRESETS: Record<string, number> = {
  "Last 24 hours": 1,
  "Last 7 days": 7,
  "Last 30 days": 30,
  "Last 90 days": 90,
};
const DEFAULT_DAYS = 7;
const iso = (date: Date) => date.toISOString().replace(/\.\d{3}Z$/, ".000Z");
const lastDays = (days: number): Pick<Filters, "start" | "end"> => {
  const end = new Date();
  return {
    start: iso(new Date(end.getTime() - days * 86_400_000)),
    end: iso(end),
  };
};
const presetFor = (filters: Filters) => {
  const days =
    (Date.parse(filters.end) - Date.parse(filters.start)) / 86_400_000;
  return (
    Object.entries(PRESETS).find(
      ([, value]) => Math.abs(value - days) < 0.001,
    )?.[0] ?? "Custom range"
  );
};

type Location =
  | { kind: "view"; view: ViewSpec }
  | { kind: "session"; harness: string; session: string }
  | { kind: "unknown" };

const readLocation = (): Location => {
  const path = window.location.pathname.replace(/^\//, "") || "pipeline";
  if (path === "session") {
    const query = new URLSearchParams(window.location.search);
    return {
      kind: "session",
      harness: query.get("harness") ?? "",
      session: query.get("session") ?? "",
    };
  }
  const view = VIEWS.find((entry) => entry.id === path);
  return view ? { kind: "view", view } : { kind: "unknown" };
};
const readFilters = (): Filters => {
  const query = new URLSearchParams(window.location.search);
  const fallback = lastDays(DEFAULT_DAYS);
  const harness = query.get("harness") ?? "";
  return {
    start: query.get("start") ?? fallback.start,
    end: query.get("end") ?? fallback.end,
    harness: (HARNESSES as readonly string[]).includes(harness)
      ? (harness as Filters["harness"])
      : "",
  };
};
const filterQuery = (filters: Filters) =>
  new URLSearchParams({
    start: filters.start,
    end: filters.end,
    harness: filters.harness,
  });

const isViewResponse = (value: unknown): value is ViewResponse =>
  isRecord(value) &&
  typeof value.view === "string" &&
  isRecord(value.data) &&
  isRecord(value.contributions) &&
  isRecord(value.filters);
const isRegistry = (value: unknown): value is Registry =>
  isRecord(value) &&
  Array.isArray(value.signals) &&
  Array.isArray(value.support) &&
  Array.isArray(value.routes) &&
  Array.isArray(value.strays);

async function getJson(url: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(url, { signal });
  const body: unknown = await response.json();
  if (!response.ok)
    throw new Error(
      isRecord(body) && typeof body.error === "string"
        ? body.error
        : "Request failed",
    );
  return body;
}

const toInput = (value: string) => value.slice(0, 16);
const fromInput = (value: string, fallback: string) =>
  value ? `${value}:00.000Z` : fallback;

export default function App() {
  const [location, setLocation] = useState<Location>(readLocation);
  const [filters, setFilters] = useState<Filters>(readFilters);
  const [custom, setCustom] = useState(
    () => presetFor(readFilters()) === "Custom range",
  );
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [response, setResponse] = useState<
    ViewResponse | SessionResponse | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState("");
  const sequence = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null);

  const navigate = useCallback(
    (path: string, next: Filters, replace = false) => {
      window.history[replace ? "replaceState" : "pushState"]({}, "", path);
      setFilters(next);
      setLocation(readLocation());
    },
    [],
  );
  const viewHref = (id: ViewId, next: Filters = filters) =>
    `/${id}?${filterQuery(next)}`;
  const sessionHref = (harness: string, session: string) =>
    `/session?${new URLSearchParams({ harness, session, ...Object.fromEntries(filterQuery(filters)) })}`;

  useEffect(() => {
    const onPop = () => {
      setLocation(readLocation());
      setFilters(readFilters());
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    getJson("/api/registry", abort.signal)
      .then((body) => {
        if (!isRegistry(body))
          throw new Error("The registry response is malformed.");
        setRegistry(body);
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError") setFailure(error.message);
      });
    return () => abort.abort();
  }, []);

  const refresh = useCallback(() => {
    const abort = new AbortController();
    const request = ++sequence.current;
    setLoading(true);
    setFailure("");
    const url =
      location.kind === "view"
        ? `/api/view?view=${location.view.id}&${filterQuery(filters)}`
        : location.kind === "session"
          ? `/api/session?${new URLSearchParams({ harness: location.harness, session: location.session })}`
          : null;
    if (!url) {
      setLoading(false);
      return () => abort.abort();
    }
    getJson(url, abort.signal)
      .then((body) => {
        if (location.kind === "view" && !isViewResponse(body))
          throw new Error("The view response is malformed.");
        if (request === sequence.current)
          setResponse(body as ViewResponse | SessionResponse);
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError" && request === sequence.current) {
          setResponse(null);
          setFailure(error.message);
        }
      })
      .finally(() => {
        if (request === sequence.current) setLoading(false);
      });
    return () => abort.abort();
  }, [filters, location]);
  useEffect(() => refresh(), [refresh]);

  const title =
    location.kind === "view"
      ? location.view.title
      : location.kind === "session"
        ? "Session"
        : "Unknown view";
  useEffect(() => {
    document.title = `${title} · Agent introspection`;
    heading.current?.focus();
  }, [title]);

  const setRange = (days: number) => {
    if (location.kind !== "view") return;
    const next = { ...filters, ...lastDays(days) };
    navigate(viewHref(location.view.id, next), next);
  };
  const change = (next: Filters) => {
    if (location.kind === "view")
      navigate(viewHref(location.view.id, next), next);
  };
  const viewResponse =
    location.kind === "view" &&
    response &&
    "view" in response &&
    response.view === location.view.id &&
    response.filters.start === filters.start &&
    response.filters.end === filters.end &&
    response.filters.harness === filters.harness
      ? response
      : null;
  const sessionResponse =
    location.kind === "session" && response && "session" in response
      ? response
      : null;
  const primary = (event: React.MouseEvent) =>
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey;

  const nav = VIEWS.map((view) => (
    <a
      key={view.id}
      className={`nav${location.kind === "view" && location.view.id === view.id ? " active" : ""}`}
      href={viewHref(view.id)}
      aria-current={
        location.kind === "view" && location.view.id === view.id
          ? "page"
          : undefined
      }
      onClick={(event) => {
        if (!primary(event)) return;
        event.preventDefault();
        navigate(viewHref(view.id), filters);
      }}
    >
      <span className="nav-num">{view.number}</span>
      <span>{view.title}</span>
    </a>
  ));

  return (
    <div className="app">
      <a className="skip" href="#content">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="mark" />
          Agent <span>introspection</span>
        </div>
        <nav aria-label="Views">{nav}</nav>
        <p className="note">
          One definition per signal for every harness. Harnesses are never
          ranked; each panel's notes say how each producer reaches the signal.
        </p>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="crumb">
            Views / <b>{title}</b>
          </div>
          <button className="refresh" onClick={() => refresh()}>
            ↻ Refresh
          </button>
        </header>
        <nav className="mobile-nav" aria-label="Views">
          {nav}
        </nav>
        <div id="content" className="content" tabIndex={-1}>
          <div className="head">
            <div>
              <div className="eyebrow">
                {location.kind === "view" ? location.view.number : "Drill-down"}
              </div>
              <h1 ref={heading} tabIndex={-1}>
                {title}
              </h1>
              <p>
                {location.kind === "view"
                  ? location.view.question
                  : location.kind === "session"
                    ? "Tasks, usage, tool calls, and user signals for one session."
                    : `${window.location.pathname} is not a view.`}
              </p>
            </div>
            <div className="live" aria-live="polite">
              {loading
                ? "Loading…"
                : failure
                  ? "Query failed"
                  : viewResponse
                    ? `Queried ${viewResponse.queriedAt.slice(11, 19)} UTC · ${viewResponse.elapsedMs} ms`
                    : sessionResponse
                      ? `Queried ${sessionResponse.queriedAt.slice(11, 19)} UTC`
                      : ""}
            </div>
          </div>
          {location.kind === "view" && (
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
                {custom && (
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
                )}
              </div>
              <HarnessSelector
                value={filters.harness}
                onChange={(harness) => change({ ...filters, harness })}
              />
              <p className="scope">
                {filters.harness === ""
                  ? "All = the union of each harness's rows for the same signal"
                  : "One harness: signals it does not emit say so, never zero"}
              </p>
            </div>
          )}
          {failure && (
            <div className="state-notice" role="alert">
              <b>Query failed — outcome unknown</b>
              <span>{failure}</span>
            </div>
          )}
          {location.kind === "unknown" && (
            <div className="state-notice" role="alert">
              <b>Unknown view</b>
              <span>Select a view from the navigation.</span>
            </div>
          )}
          {registry && location.kind === "view" && viewResponse && (
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
          )}
          {registry && location.kind === "session" && sessionResponse && (
            <ViewContext.Provider
              value={{ registry, contributions: {}, filters, sessionHref }}
            >
              <Session response={sessionResponse} />
            </ViewContext.Provider>
          )}
          {loading && !viewResponse && !sessionResponse && !failure && (
            <div className="skeleton" aria-label="Loading">
              <i />
              <i />
              <i />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
