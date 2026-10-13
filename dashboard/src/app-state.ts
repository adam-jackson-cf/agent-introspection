import {
  HARNESSES,
  type Filters,
  type Registry,
  type ViewResponse,
} from "./contracts";
import { isRecord } from "./type-guards";

export const PRESETS: Record<string, number> = {
  "Last 24 hours": 1,
  "Last 7 days": 7,
  "Last 30 days": 30,
  "Last 90 days": 90,
};
const DEFAULT_DAYS = 7;
const iso = (date: Date) => date.toISOString().replace(/\.\d{3}Z$/, ".000Z");
export const lastDays = (days: number): Pick<Filters, "start" | "end"> => {
  const end = new Date();
  return {
    start: iso(new Date(end.getTime() - days * 86_400_000)),
    end: iso(end),
  };
};
/** The selector label for the filters: a live preset, or a fixed custom range. */
export const presetFor = (filters: Filters) =>
  Object.entries(PRESETS).find(([, days]) => days === filters.range)?.[0] ??
  "Custom range";
/** Reads the URL: `range` is a live preset; `start`/`end` a fixed range; neither the default preset. */
export const readFilters = (): Filters => {
  const query = new URLSearchParams(window.location.search);
  const harness = query.get("harness") ?? "";
  const scope = {
    harness: (HARNESSES as readonly string[]).includes(harness)
      ? (harness as Filters["harness"])
      : ("" as const),
  };
  const range = Number(query.get("range"));
  const start = query.get("start");
  const end = query.get("end");
  if (Object.values(PRESETS).includes(range))
    return { ...lastDays(range), ...scope, range };
  if (start && end) return { start, end, ...scope };
  return { ...lastDays(DEFAULT_DAYS), ...scope, range: DEFAULT_DAYS };
};
/** Filters as URL parameters: a live preset keeps only its length; All has no `harness`. */
export const filterQuery = (filters: Filters) => {
  const query = new URLSearchParams(
    filters.range === undefined
      ? { start: filters.start, end: filters.end }
      : { range: String(filters.range) },
  );
  if (filters.harness !== "") query.set("harness", filters.harness);
  return query;
};
/** Filters as API parameters: always the resolved window. */
export const apiQuery = (filters: Filters) =>
  new URLSearchParams({
    start: filters.start,
    end: filters.end,
    harness: filters.harness,
  });

export const isViewResponse = (value: unknown): value is ViewResponse =>
  isRecord(value) &&
  typeof value.view === "string" &&
  isRecord(value.data) &&
  isRecord(value.errors) &&
  isRecord(value.contributions) &&
  isRecord(value.filters);
export const isRegistry = (value: unknown): value is Registry =>
  isRecord(value) &&
  Array.isArray(value.signals) &&
  Array.isArray(value.support) &&
  Array.isArray(value.routes) &&
  Array.isArray(value.strays) &&
  Array.isArray(value.exclusions) &&
  Array.isArray(value.harnesses) &&
  Array.isArray(value.hidden);

export async function getJson(
  url: string,
  signal: AbortSignal,
): Promise<unknown> {
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

export const toInput = (value: string) => value.slice(0, 16);
export const fromInput = (value: string, fallback: string) =>
  value ? `${value}:00.000Z` : fallback;
