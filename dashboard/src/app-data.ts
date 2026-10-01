import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Filters,
  Registry,
  SessionResponse,
  ViewResponse,
} from "./contracts";
import {
  filterQuery,
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
    return `/api/view?view=${location.view.id}&${filterQuery(filters)}`;
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

/** Registry plus the response for the current location, newest request wins. */
export function useAppData(location: Location, filters: Filters) {
  const [response, setResponse] = useState<AppResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewFailure, setFailure] = useState("");
  const sequence = useRef(0);
  const { registry, registryFailure, reload: reloadRegistry } = useRegistry();
  const failure = visibleFailure(registryFailure, viewFailure);

  const load = useCallback(() => {
    const abort = new AbortController();
    const request = ++sequence.current;
    setLoading(true);
    setFailure("");
    const url = requestUrl(location, filters);
    if (!url) {
      setLoading(false);
      return () => abort.abort();
    }
    getJson(url, abort.signal)
      .then((body) => {
        if (location.kind === "view" && !isViewResponse(body))
          throw new Error("The view response is malformed.");
        if (request === sequence.current) setResponse(body as AppResponse);
      })
      .catch((error: Error) => {
        if (!isAbort(error) && request === sequence.current) {
          setResponse(null);
          setFailure(error.message);
        }
      })
      .finally(() => {
        if (request === sequence.current) setLoading(false);
      });
    return () => abort.abort();
  }, [filters, location]);
  useEffect(() => load(), [load]);

  const viewResponse = matchingView(location, response, filters);
  const sessionResponse = matchingSession(location, response);
  const refresh = useCallback(() => {
    reloadRegistry();
    return load();
  }, [reloadRegistry, load]);
  return { registry, loading, failure, refresh, viewResponse, sessionResponse };
}
