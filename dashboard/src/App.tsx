import {
  Component,
  type ErrorInfo,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  CANONICAL_ROUTES,
  type DashboardResponse,
  type FilterState,
  type ResultState,
  type RouteId,
  type WidgetResult,
} from "./contracts";
import { PRESENTATION, type PanelPresentation } from "./presentation";
import { isPipelinePanel, PipelineMeasurement } from "./pipeline";

const NAV = [
  {
    id: "pipeline",
    number: "01",
    short: "Pipeline",
    detail: "Capture, correlation, attribution",
  },
  {
    id: "provider",
    number: "02",
    short: "Provider",
    detail: "Requests, latency, retries",
  },
  {
    id: "usage",
    number: "03a",
    short: "Usage",
    detail: "Calls, tokens, explicit friction",
  },
  {
    id: "tools",
    number: "03b",
    short: "Tools",
    detail: "Failures, recovery, loops",
  },
  {
    id: "recurrence",
    number: "03c",
    short: "Recurrence",
    detail: "Patterns, practices, outcomes",
  },
] as const;

const iso = (date: Date) => date.toISOString();
const defaultFilters = (): FilterState => ({
  start: iso(new Date(Date.now() - 86_400_000)),
  end: iso(new Date()),
  provider: "All providers",
  modelRole: "Requested",
  model: "All models",
  project: "All projects",
});
const routeFor = (id: RouteId) =>
  CANONICAL_ROUTES.find((route) => route.id === id)!;
const routeFromPath = (): RouteId | null =>
  CANONICAL_ROUTES.find((route) => route.path === location.pathname)?.id ??
  null;
const filterKeys = [
  "start",
  "end",
  "provider",
  "modelRole",
  "model",
  "project",
] as const;
const parseFilters = (): FilterState => {
  const query = new URLSearchParams(location.search);
  const fallback = defaultFilters();
  return Object.fromEntries(
    filterKeys.map((key) => [key, query.get(key) ?? fallback[key]]),
  ) as FilterState;
};
const filterErrors = (filters: FilterState) => {
  const errors: string[] = [];
  const query = new URLSearchParams(location.search);
  const unknownKeys = [...query.keys()].filter(
    (key, index, keys) =>
      !filterKeys.includes(key as (typeof filterKeys)[number]) &&
      keys.indexOf(key) === index,
  );
  if (unknownKeys.length > 0)
    errors.push(`Unknown URL filter: ${unknownKeys.join(", ")}`);
  for (const key of filterKeys)
    if (query.getAll(key).length > 1)
      errors.push(`Repeated URL filter: ${key}`);
  const start = Date.parse(filters.start);
  const end = Date.parse(filters.end);
  if (Number.isNaN(start))
    errors.push(`Invalid start value in URL: ${filters.start}`);
  if (Number.isNaN(end))
    errors.push(`Invalid end value in URL: ${filters.end}`);
  if (!Number.isNaN(start) && !Number.isNaN(end) && start >= end)
    errors.push("Start time must be earlier than end time.");
  if (filters.modelRole !== "Requested" && filters.modelRole !== "Response")
    errors.push(`Invalid model role in URL: ${filters.modelRole}`);
  return errors;
};
const isDashboardResponse = (value: unknown): value is DashboardResponse => {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return false;
  const response = value as {
    route?: unknown;
    filters?: unknown;
    widgets?: unknown;
    queriedAt?: unknown;
    transport?: unknown;
  };
  if (
    typeof response.route !== "object" ||
    response.route === null ||
    Array.isArray(response.route) ||
    typeof response.filters !== "object" ||
    response.filters === null ||
    Array.isArray(response.filters) ||
    !Array.isArray(response.widgets) ||
    typeof response.transport !== "object" ||
    response.transport === null ||
    Array.isArray(response.transport) ||
    typeof response.queriedAt !== "string"
  )
    return false;
  const route = response.route as Record<string, unknown>;
  const filters = response.filters as Record<string, unknown>;
  const transport = response.transport as {
    state?: unknown;
    message?: unknown;
  };
  if (
    !CANONICAL_ROUTES.some(
      (candidate) =>
        candidate.id === route.id &&
        candidate.path === route.path &&
        candidate.title === route.title &&
        candidate.eyebrow === route.eyebrow &&
        candidate.subtitle === route.subtitle,
    )
  )
    return false;
  if (
    Number.isNaN(Date.parse(response.queriedAt)) ||
    !filterKeys.every((key) => typeof filters[key] === "string")
  )
    return false;
  if (filters.modelRole !== "Requested" && filters.modelRole !== "Response")
    return false;
  if (
    (transport.state !== "Data" && transport.state !== "Query/system error") ||
    typeof transport.message !== "string"
  )
    return false;
  return response.widgets.every((value) => {
    if (typeof value !== "object" || value === null || Array.isArray(value))
      return false;
    const result = value as {
      widget?: unknown;
      gate?: unknown;
      measurement?: unknown;
    };
    if (
      typeof result.widget !== "object" ||
      result.widget === null ||
      Array.isArray(result.widget) ||
      typeof result.gate !== "object" ||
      result.gate === null ||
      Array.isArray(result.gate)
    )
      return false;
    const widget = result.widget as Record<string, unknown>;
    const gate = result.gate as Record<string, unknown>;
    if (
      !["id", "title", "section", "type", "contract"].every(
        (key) => typeof widget[key] === "string",
      ) ||
      !["measureIds", "matrixRowIds"].every(
        (key) =>
          Array.isArray(widget[key]) &&
          (widget[key] as unknown[]).every((item) => typeof item === "string"),
      ) ||
      !Array.isArray(widget.layout) ||
      widget.layout.length !== 4 ||
      !widget.layout.every((item) => typeof item === "number") ||
      !Array.isArray(widget.independentGates) ||
      !widget.independentGates.every((value) => {
        if (typeof value !== "object" || value === null || Array.isArray(value))
          return false;
        const gate = value as { id?: unknown; measureId?: unknown };
        return (
          typeof gate.id === "string" && typeof gate.measureId === "string"
        );
      }) ||
      !["Blocked", "Data", "Not applicable"].includes(gate.status as string) ||
      !Array.isArray(gate.reasons) ||
      !gate.reasons.every((item) => typeof item === "string") ||
      !Array.isArray(gate.proofs)
    )
      return false;
    if (
      result.measurement !== undefined &&
      (route.id !== "pipeline" || !isPipelinePanel(result.measurement))
    )
      return false;
    return gate.proofs.every((value) => {
      if (typeof value !== "object" || value === null || Array.isArray(value))
        return false;
      const proof = value as Record<string, unknown>;
      return (
        typeof proof.producer === "string" &&
        ["Blocked", "Proven", "Not applicable"].includes(
          proof.status as string,
        ) &&
        typeof proof.reason === "string" &&
        (proof.rowId === undefined || typeof proof.rowId === "string") &&
        (proof.independentGate === undefined ||
          typeof proof.independentGate === "string") &&
        (proof.authority === undefined || typeof proof.authority === "string")
      );
    });
  });
};
const toLocalInput = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? ""
    : `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}T${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
};
const toUtc = (value: string, fallback: string) =>
  value ? new Date(`${value}Z`).toISOString() : fallback;
const presetForRange = (filters: FilterState) => {
  const duration = Date.parse(filters.end) - Date.parse(filters.start);
  const presets: Record<string, number> = {
    "Last 24 hours": 24,
    "Last 7 days": 168,
    "Last 30 days": 720,
  };
  return (
    Object.entries(presets).find(
      ([, hours]) =>
        Number.isFinite(duration) &&
        Math.abs(duration - hours * 3_600_000) <= 1_000,
    )?.[0] ?? "Custom range"
  );
};
const routeHref = (routeId: RouteId, filters: FilterState) =>
  `${routeFor(routeId).path}?${new URLSearchParams(filters)}`;
const primaryNavigation = (event: React.MouseEvent<HTMLAnchorElement>) =>
  event.button === 0 &&
  !event.metaKey &&
  !event.ctrlKey &&
  !event.shiftKey &&
  !event.altKey;

class Boundary extends Component<
  { children: ReactNode; label: string },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {}
  render() {
    return this.state.failed ? (
      <section className="boundary" role="alert">
        <b>{this.props.label} could not be displayed.</b>
        <span>Query failed — outcome unknown</span>
      </section>
    ) : (
      this.props.children
    );
  }
}

function Missing({ message }: { message: string }) {
  return (
    <div className="missing">
      <img src="/missing-data.svg" alt="Missing data illustration" />
      <div>
        <strong>Missing data</strong>
        <p>{message}</p>
      </div>
    </div>
  );
}
function StateRenderer({ state }: { state: ResultState }) {
  if (state === "Loading")
    return (
      <div className="skeleton" aria-label="Loading result">
        <i />
        <i />
        <i />
      </div>
    );
  if (state === "Data")
    return (
      <Missing message="The response declares data but contains no typed renderable value." />
    );
  const text: Record<Exclude<ResultState, "Loading" | "Data">, string> = {
    "No data": "No data for the selected filters and range",
    "Not applicable": "Not applicable",
    Unavailable: "Unavailable",
    "Integrity failure": "Results withheld",
    "Query/system error": "Query failed — outcome unknown",
  };
  return (
    <div className="state-notice" role="status">
      <b>{text[state]}</b>
    </div>
  );
}
function Panel({
  result,
  loading,
  presentation,
  additional = false,
}: {
  result: WidgetResult;
  loading: boolean;
  presentation?: PanelPresentation;
  additional?: boolean;
}) {
  const { widget, gate } = result;
  const [details, setDetails] = useState(false);
  const title = presentation?.title ?? widget.title;
  return (
    <Boundary label={title}>
      <article
        className={`panel${additional ? " additional-panel" : ""}`}
        data-widget-id={widget.id}
        data-design-span={presentation?.span ?? 6}
        {...(presentation ? { "data-design-height": presentation.height } : {})}
      >
        <header className="panel-head">
          <div>
            <h2>{title}</h2>
            <p>{presentation?.description ?? "Canonical measurement"}</p>
          </div>
          <div className="panel-actions">
            <span className="tag">
              {widget.id === "p1-snapshot"
                ? "P1 · P3 · P10"
                : widget.measureIds.join(" · ")}
            </span>
            <button
              className="details"
              aria-label={
                details ? "Hide contract details" : "View contract details"
              }
              title={
                details ? "Hide contract details" : "View contract details"
              }
              aria-expanded={details}
              onClick={() => setDetails((value) => !value)}
            >
              <span aria-hidden="true">i</span>
            </button>
          </div>
        </header>
        <div className="panel-body">
          {loading ? (
            <StateRenderer state="Loading" />
          ) : result.measurement ? (
            <PipelineMeasurement
              panel={result.measurement}
              widgetId={widget.id}
            />
          ) : gate.status === "Data" ? (
            <StateRenderer state="Data" />
          ) : gate.status === "Not applicable" ? (
            <StateRenderer state="Not applicable" />
          ) : (
            <Missing message="The canonical production population is not yet available." />
          )}
        </div>
        {details && (
          <footer className="panel-footer">
            <div className="contract-detail">
              <b>Canonical title</b>
              <p>{widget.title}</p>
              <b>Canonical contract</b>
              <p>{widget.contract}</p>
              <b>Gate status</b>
              <p>{gate.status}</p>
              <b>Gate reasons</b>
              <p>
                {gate.reasons.join(" ") ||
                  (gate.status === "Data"
                    ? "Application production gate proven"
                    : "The canonical production population is not yet proven.")}
              </p>
              {result.measurement && (
                <>
                  <b>Application measurement provenance</b>
                  <p>
                    This panel is displayed because its application production
                    gate is proven by the authoritative validator.
                  </p>
                  <PipelineMeasurement
                    panel={result.measurement}
                    widgetId={widget.id}
                    details
                  />
                </>
              )}
              <b>Coverage and gate IDs</b>
              <p>
                {[
                  ...widget.measureIds,
                  ...widget.matrixRowIds,
                  ...widget.independentGates.map((gate) => gate.id),
                ].join(", ")}
              </p>
              {gate.proofs.map((proof) => (
                <p
                  className="proof"
                  key={`${proof.producer}-${proof.rowId ?? proof.independentGate}`}
                >
                  {proof.producer}: {proof.status} — {proof.reason} (
                  {proof.rowId ?? proof.independentGate}
                  {proof.authority ? ` · ${proof.authority}` : ""})
                </p>
              ))}
            </div>
          </footer>
        )}
      </article>
    </Boundary>
  );
}
function ControlMissing({ label, value }: { label: string; value: string }) {
  const id = `${label.toLowerCase().replace(/\s+/g, "-")}-missing`;
  const unavailable = "Missing data — authoritative options are not available.";
  return (
    <div className="filter missing-control">
      <label htmlFor={id} title={unavailable}>
        {label}
        <span className="control-missing">
          <img src="/missing-data.svg" alt="" />
          Missing data
        </span>
      </label>
      <select
        id={id}
        value={value}
        disabled
        aria-label={`${label}: ${unavailable}`}
      >
        <option value={value}>{value}</option>
      </select>
    </div>
  );
}

export default function App() {
  const [routeId, setRouteId] = useState<RouteId | null>(routeFromPath);
  const [filters, setFilters] = useState<FilterState>(parseFilters);
  const [showCustomRange, setShowCustomRange] = useState(
    () => presetForRange(parseFilters()) === "Custom range",
  );
  const [response, setResponse] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState("");
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const previousRoute = useRef<RouteId | null>(routeId);
  const route = routeId ? routeFor(routeId) : null;
  const errors = filterErrors(filters);
  const updateLocation = useCallback(
    (nextRoute: RouteId, nextFilters: FilterState, replace = false) => {
      history[replace ? "replaceState" : "pushState"](
        {},
        "",
        routeHref(nextRoute, nextFilters),
      );
      setRouteId(nextRoute);
      setFilters(nextFilters);
    },
    [],
  );
  const navigate = useCallback(
    (id: RouteId) => updateLocation(id, filters),
    [filters, updateLocation],
  );
  const refresh = useCallback(async () => {
    if (!routeId || errors.length > 0) {
      setLoading(false);
      return;
    }
    controller.current?.abort();
    const request = ++sequence.current;
    const abort = new AbortController();
    controller.current = abort;
    setLoading(true);
    setFailure("");
    const query = new URLSearchParams({ route: routeId, ...filters });
    try {
      const res = await fetch(`/api/dashboard?${query}`, {
        signal: abort.signal,
      });
      if (!res.ok)
        throw new Error("Dashboard service did not return a valid result.");
      const payload: unknown = await res.json();
      if (!isDashboardResponse(payload))
        throw new Error("Dashboard service returned malformed data.");
      if (request === sequence.current) setResponse(payload);
    } catch (error) {
      if (
        (error as Error).name !== "AbortError" &&
        request === sequence.current
      ) {
        setResponse(null);
        setFailure("Query failed — outcome unknown");
      }
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }, [errors.length, filters, routeId]);
  useEffect(() => {
    const query = new URLSearchParams(location.search);
    if (routeId && !filterKeys.every((key) => query.has(key))) {
      for (const key of filterKeys)
        if (!query.has(key)) query.set(key, filters[key]);
      history.replaceState({}, "", `${routeFor(routeId).path}?${query}`);
    }
  }, [filters, routeId]);
  useEffect(() => {
    void refresh();
    return () => controller.current?.abort();
  }, [refresh]);
  useEffect(() => {
    const onPop = () => {
      const nextRoute = routeFromPath();
      const nextFilters = parseFilters();
      setRouteId(nextRoute);
      setFilters(nextFilters);
      setShowCustomRange(presetForRange(nextFilters) === "Custom range");
    };
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);
  useEffect(() => {
    document.title = route
      ? `${route.title} · SigNoz Dashboards`
      : "Unknown dashboard route · SigNoz Dashboards";
    if (routeId && previousRoute.current !== routeId) heading.current?.focus();
    previousRoute.current = routeId;
  }, [route, routeId]);
  const matchingResponse =
    response?.route.id === routeId &&
    (Object.keys(filters) as (keyof FilterState)[]).every(
      (key) => response.filters[key] === filters[key],
    )
      ? response
      : null;
  const widgets = matchingResponse?.widgets ?? [];
  const presentation = routeId ? PRESENTATION[routeId] : null;
  const primaryWidgetIds =
    presentation?.sections.flatMap((section) =>
      section.panels.map((panel) => panel.widgetId),
    ) ?? [];
  const additionalWidgets = widgets.filter(
    (result) => !primaryWidgetIds.includes(result.widget.id),
  );
  const selectedRange = presetForRange(filters);
  const change = <K extends keyof FilterState>(key: K, value: FilterState[K]) =>
    routeId && updateLocation(routeId, { ...filters, [key]: value });
  const setPreset = (hours: number) => {
    if (!routeId) return;
    const end = new Date();
    updateLocation(routeId, {
      ...filters,
      start: iso(new Date(end.valueOf() - hours * 3_600_000)),
      end: iso(end),
    });
  };
  if (!route) {
    return (
      <main className="invalid-route">
        <div className="brand">
          <span className="mark" />
          SigNoz <span>Dashboards</span>
        </div>
        <section className="state-notice" role="alert">
          <b>Unknown dashboard route</b>
          <span>
            {location.pathname} is not a dashboard route. Select a canonical
            dashboard view.
          </span>
        </section>
        <nav aria-label="Dashboard views">
          {NAV.map((entry) => (
            <a
              key={entry.id}
              className="nav"
              href={routeHref(entry.id, filters)}
            >
              {entry.number} {entry.short}
            </a>
          ))}
        </nav>
      </main>
    );
  }
  return (
    <div className="app">
      <a className="skip" href="#dashboard-content">
        Skip to dashboard content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="mark" />
          SigNoz <span>Dashboards</span>
        </div>
        <nav aria-label="Dashboard views">
          <div className="nav-label">Operational</div>
          {NAV.map((entry, index) => (
            <div key={entry.id}>
              {index === 2 && <div className="nav-label">Model usage</div>}
              <a
                className={`nav ${routeId === entry.id ? "active" : ""}`}
                href={routeHref(entry.id, filters)}
                onClick={(event) => {
                  if (primaryNavigation(event)) {
                    event.preventDefault();
                    navigate(entry.id);
                  }
                }}
                aria-current={routeId === entry.id ? "page" : undefined}
              >
                <span className="nav-num">{entry.number}</span>
                <span>
                  <b>
                    {entry.short === "Usage"
                      ? "Usage & interaction"
                      : entry.short === "Tools"
                        ? "Tool execution"
                        : entry.short === "Recurrence"
                          ? "Recurrence & interventions"
                          : `${entry.short} health`}
                  </b>
                  <small>{entry.detail}</small>
                </span>
              </a>
            </div>
          ))}
        </nav>
        <div className="note">
          {matchingResponse?.widgets.some((widget) => widget.measurement)
            ? "Qualified application measurements are shown. Missing-data images preserve every other widget position."
            : "Missing-data images preserve every widget position. Expand contract details for blockers and provenance."}
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="crumb">
            Dashboards / <b>{presentation?.title ?? route.title}</b>
          </div>
          <button
            className="refresh"
            onClick={() => void refresh()}
            aria-label="Refresh dashboard"
          >
            ↻ Refresh
          </button>
        </header>
        <nav className="mobile-nav" aria-label="Dashboard views">
          {NAV.map((entry) => (
            <a
              key={entry.id}
              href={routeHref(entry.id, filters)}
              onClick={(event) => {
                if (primaryNavigation(event)) {
                  event.preventDefault();
                  navigate(entry.id);
                }
              }}
              className={routeId === entry.id ? "active" : ""}
              aria-current={routeId === entry.id ? "page" : undefined}
            >
              {entry.number} {entry.short}
            </a>
          ))}
        </nav>
        <div id="dashboard-content" className="content" tabIndex={-1}>
          <div className="head">
            <div>
              <div className="eyebrow">
                {presentation?.eyebrow ?? route.eyebrow}
              </div>
              <h1 ref={heading} tabIndex={-1}>
                {presentation?.title ?? route.title}
              </h1>
              <p>{presentation?.subtitle ?? route.subtitle}</p>
            </div>
            <div
              className={`live ${matchingResponse?.transport.state === "Data" && routeId === "pipeline" && widgets.some((widget) => widget.measurement) ? "live-data" : ""}`}
              aria-live="polite"
              title={matchingResponse?.queriedAt}
            >
              {failure ||
                (matchingResponse?.transport.state === "Query/system error"
                  ? "Query failed — outcome unknown"
                  : routeId === "pipeline" &&
                      widgets.some((widget) => widget.measurement)
                    ? "Production pipeline measurements"
                    : "Missing data · production measurements withheld")}
            </div>
          </div>
          <div className="filters" aria-label="Dashboard filters">
            <div className="filter">
              <label htmlFor="time-range">Time range · UTC</label>
              <select
                id="time-range"
                value={showCustomRange ? "Custom range" : selectedRange}
                onChange={(event) => {
                  if (event.target.value === "Custom range") {
                    setShowCustomRange(true);
                    return;
                  }
                  setShowCustomRange(false);
                  const hours: Record<string, number> = {
                    "Last 24 hours": 24,
                    "Last 7 days": 168,
                    "Last 30 days": 720,
                  };
                  const preset = hours[event.target.value];
                  if (preset) setPreset(preset);
                }}
              >
                <option>Last 24 hours</option>
                <option>Last 7 days</option>
                <option>Last 30 days</option>
                <option>Custom range</option>
              </select>
              {showCustomRange && (
                <div className="custom-range">
                  <input
                    type="datetime-local"
                    value={toLocalInput(filters.start)}
                    onChange={(event) =>
                      change("start", toUtc(event.target.value, filters.start))
                    }
                    aria-label="Start time in UTC"
                  />
                  <input
                    type="datetime-local"
                    value={toLocalInput(filters.end)}
                    onChange={(event) =>
                      change("end", toUtc(event.target.value, filters.end))
                    }
                    aria-label="End time in UTC"
                  />
                </div>
              )}
            </div>
            {route.id === "provider" && (
              <>
                <ControlMissing label="Provider" value={filters.provider} />
                <div className="filter">
                  <label htmlFor="model-role">Model role</label>
                  <select
                    id="model-role"
                    value={filters.modelRole}
                    onChange={(event) =>
                      change(
                        "modelRole",
                        event.target.value as FilterState["modelRole"],
                      )
                    }
                  >
                    {filters.modelRole !== "Requested" &&
                      filters.modelRole !== "Response" && (
                        <option value={filters.modelRole}>
                          {filters.modelRole} (invalid URL value)
                        </option>
                      )}
                    <option>Requested</option>
                    <option>Response</option>
                  </select>
                </div>
                <ControlMissing label="Model" value={filters.model} />
                {filters.model !== "All models" && (
                  <button
                    className="reset"
                    onClick={() => change("model", "All models")}
                  >
                    Reset model
                  </button>
                )}
              </>
            )}
            {(["usage", "tools", "recurrence"] as RouteId[]).includes(
              route.id,
            ) && <ControlMissing label="Project" value={filters.project} />}
            <div className="scope">
              {route.id === "provider"
                ? "Exact Provider + Model role + Model identity · UTC"
                : ["usage", "tools", "recurrence"].includes(route.id)
                  ? "All projects includes Unresolved · canonical project ID · UTC"
                  : "Range basis varies by labeled section · UTC"}
            </div>
          </div>
          {errors.length > 0 && (
            <div className="state-notice query-error" role="alert">
              <b>Invalid URL selection</b>
              <span>{errors.join(" ")}</span>
            </div>
          )}
          <Boundary label="Dashboard route">
            {failure ||
            matchingResponse?.transport.state === "Query/system error" ? (
              <div className="query-error">
                <StateRenderer state="Query/system error" />
              </div>
            ) : null}
            {loading && widgets.length === 0 ? (
              <div className="grid">
                <article className="panel loading-panel">
                  <StateRenderer state="Loading" />
                </article>
              </div>
            ) : widgets.length === 0 ? (
              <div className="grid">
                <article className="panel loading-panel">
                  <Missing
                    message={
                      errors.length > 0
                        ? "Correct the URL selection to request this dashboard scope."
                        : "No scoped dashboard result is available."
                    }
                  />
                </article>
              </div>
            ) : (
              <>
                {presentation?.sections.map((section) => {
                  const panelResults = section.panels.flatMap((panel) => {
                    const result = widgets.find(
                      (candidate) => candidate.widget.id === panel.widgetId,
                    );
                    return result ? [[result, panel] as const] : [];
                  });
                  return (
                    <section className="widget-section" key={section.title}>
                      <h2>{section.title}</h2>
                      <div className="grid">
                        {panelResults.map(([result, panel]) => (
                          <Panel
                            key={result.widget.id}
                            result={result}
                            loading={loading}
                            presentation={panel}
                          />
                        ))}
                      </div>
                    </section>
                  );
                })}
                {additionalWidgets.length > 0 && (
                  <section className="widget-section additional-measurements">
                    <h2>Additional measurements</h2>
                    <div className="grid">
                      {additionalWidgets.map((result) => (
                        <Panel
                          key={result.widget.id}
                          result={result}
                          loading={loading}
                          additional
                        />
                      ))}
                    </div>
                  </section>
                )}
                <footer className="reading-order">
                  Panels are arranged in the reading order of this dashboard.
                </footer>
              </>
            )}
          </Boundary>
        </div>
      </main>
    </div>
  );
}
