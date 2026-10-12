import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Filters,
  Registry,
  SessionResponse,
  ViewResponse,
} from "./contracts";
import {
  apiQuery,
  getJson,
  isRegistry,
  isViewResponse,
  readFilters,
} from "./app-state";
import { readLocation, type Location } from "./routes";

type AppResponse = ViewResponse | SessionResponse;

/** Current location and filters, kept in step with the browser history. */
export function useRouting() {
  const [location, setLocation] = useState<Location>(readLocation);
  const [filters, setFilters] = useState<Filters>(readFilters);
  const navigate = useCallback(
    (path: string, next: Filters, replace = false) => {
      window.history[replace ? "replaceState" : "pushState"]({}, "", path);
      setFilters(next);
      setLocation(readLocation());
    },
    [],
  );
  useEffect(() => {
    const onPop = () => {
      setLocation(readLocation());
      setFilters(readFilters());
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return { location, filters, navigate };
}

const requestUrl = (location: Location, filters: Filters) => {
  if (location.kind === "view")
    return `/api/view?view=${location.view.id}&${apiQuery(filters)}`;
  if (location.kind === "session")
    return `/api/session?${new URLSearchParams({ harness: location.harness, session: location.session })}`;
  return null;
};

const isAbort = (error: Error) => error.name === "AbortError";

export const matchingView = (
  location: Location,
  response: AppResponse | null,
  filters: Filters,
) =>
  location.kind === "view" &&
  response &&
  "view" in response &&
  response.view === location.view.id &&
  response.filters.start === filters.start &&
  response.filters.end === filters.end &&
  response.filters.harness === filters.harness
    ? response
    : null;

export const matchingSession = (
  location: Location,
  response: AppResponse | null,
) =>
  location.kind === "session" &&
  response &&
  "session" in response &&
  response.harness === location.harness &&
  response.session === location.session
    ? response
    : null;

/** The visible failure: a registry that has not loaded outranks a view failure. */
export const visibleFailure = (registryFailure: string, viewFailure: string) =>
  registryFailure || viewFailure;

/** Loads the signal registry; `reload` retries it only while it is absent. */
function useRegistry() {
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [failure, setFailure] = useState("");
  const [attempt, setAttempt] = useState(0);
  const loaded = useRef(false);
  useEffect(() => {
    const abort = new AbortController();
    getJson("/api/registry", abort.signal)
      .then((body) => {
        if (!isRegistry(body))
          throw new Error("The registry response is malformed.");
        loaded.current = true;
        setRegistry(body);
        setFailure("");
      })
      .catch((error: Error) => {
        if (!isAbort(error)) setFailure(error.message);
      });
    return () => abort.abort();
  }, [attempt]);
  const reload = useCallback(() => {
    if (!loaded.current) setAttempt((count) => count + 1);
  }, []);
  return { registry, registryFailure: failure, reload };
}

/** Longest a view may take before the request is abandoned (user decision, 2026-10-02). */
export const REQUEST_TIMEOUT_MS = 120_000;
const TIMEOUT_MESSAGE = `Query timed out after ${REQUEST_TIMEOUT_MS / 1000} s`;

/** Whole seconds since `loading` began; 0 when idle. */
function useElapsed(loading: boolean) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (!loading) return;
    const began = performance.now();
    const timer = setInterval(
      () => setSeconds(Math.floor((performance.now() - began) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [loading]);
  return seconds;
}

/** The first view response of the page load marks `view-ready`, measured from navigation start. */
function markViewReady() {
  if (performance.getEntriesByName("view-ready").length > 0) return;
  performance.mark("view-ready");
  performance.measure("view-ready", { start: 0, end: "view-ready" });
}

/** Registry plus the response for the current location, newest request wins. */
export function useAppData(location: Location, filters: Filters) {
  const [response, setResponse] = useState<AppResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewFailure, setFailure] = useState("");
  const sequence = useRef(0);
  const { registry, registryFailure, reload: reloadRegistry } = useRegistry();
  const failure = visibleFailure(registryFailure, viewFailure);
  const elapsedSeconds = useElapsed(loading);

  // The registry and the view load side by side; neither waits for the other.
  const load = useCallback(() => {
    const abort = new AbortController();
    const request = ++sequence.current;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      abort.abort();
    }, REQUEST_TIMEOUT_MS);
    setLoading(true);
    setFailure("");
    const url = requestUrl(location, filters);
    if (!url) {
      clearTimeout(timer);
      setLoading(false);
      return () => abort.abort();
    }
    getJson(url, abort.signal)
      .then((body) => {
        if (location.kind === "view" && !isViewResponse(body))
          throw new Error("The view response is malformed.");
        if (request === sequence.current) {
          setResponse(body as AppResponse);
          markViewReady();
        }
      })
      .catch((error: Error) => {
        if (request !== sequence.current) return;
        if (timedOut) setFailure(TIMEOUT_MESSAGE);
        else if (!isAbort(error)) setFailure(error.message);
      })
      .finally(() => {
        clearTimeout(timer);
        if (request === sequence.current) setLoading(false);
      });
    return () => abort.abort();
  }, [filters, location]);
  useEffect(() => load(), [load]);

  const viewResponse = matchingView(location, response, filters);
  const sessionResponse = matchingSession(location, response);
  // After a failed refresh the last good response of this view stays on screen, dimmed.
  const lastGoodView =
    failure &&
    !viewResponse &&
    location.kind === "view" &&
    response &&
    "view" in response &&
    response.view === location.view.id
      ? response
      : null;
  const awaiting = loading && !viewResponse && !sessionResponse && !failure;
  const refresh = useCallback(() => {
    reloadRegistry();
    return load();
  }, [reloadRegistry, load]);
  return {
    registry,
    loading,
    elapsedSeconds,
    failure,
    refresh,
    reloadRegistry,
    viewResponse,
    lastGoodView,
    awaiting,
    sessionResponse,
  };
}
