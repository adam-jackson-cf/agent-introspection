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
/** A preset is live only when the window ends at (about) the current time. */
const LIVE_END_TOLERANCE_MS = 5 * 60_000;
export const presetFor = (filters: Filters, now = Date.now()) => {
  const end = Date.parse(filters.end);
  const days = (end - Date.parse(filters.start)) / 86_400_000;
  if (Math.abs(now - end) > LIVE_END_TOLERANCE_MS) return "Custom range";
  return (
    Object.entries(PRESETS).find(
      ([, value]) => Math.abs(value - days) < 0.001,
    )?.[0] ?? "Custom range"
  );
};
export const readFilters = (): Filters => {
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
export const filterQuery = (filters: Filters) =>
  new URLSearchParams({
    start: filters.start,
    end: filters.end,
    harness: filters.harness,
  });

export const isViewResponse = (value: unknown): value is ViewResponse =>
  isRecord(value) &&
  typeof value.view === "string" &&
  isRecord(value.data) &&
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
