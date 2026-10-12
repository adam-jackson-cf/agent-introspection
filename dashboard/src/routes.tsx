import { type ComponentType } from "react";
import { type Row, type ViewId, type ViewPath } from "./contracts";
import { HEALTH_VIEWS } from "./routes-health";
import { OUTCOME_VIEWS } from "./routes-outcomes";

export type DetailSpec = {
  /** The path segment under the parent view, as in `/pipeline/coverage`. */
  id: string;
  title: string;
  question: string;
  component: ComponentType<{ data: Record<string, Row[]> }>;
};

export type ViewSpec = {
  id: ViewId;
  number: string;
  title: string;
  question: string;
  component: ComponentType<{ data: Record<string, Row[]> }>;
  /** L3 pages of this view: same nav item, filters and data. */
  details?: DetailSpec[];
};

/** Question-led views, in reading order. */
export const VIEWS: ViewSpec[] = [...HEALTH_VIEWS, ...OUTCOME_VIEWS];

export type Location =
  | { kind: "view"; view: ViewSpec; detail?: DetailSpec }
  | { kind: "session"; harness: string; session: string }
  | { kind: "unknown" };

export const readLocation = (): Location => {
  const path = window.location.pathname.replace(/^\//, "") || "pipeline";
  if (path === "session") {
    const query = new URLSearchParams(window.location.search);
    return {
      kind: "session",
      harness: query.get("harness") ?? "",
      session: query.get("session") ?? "",
    };
  }
  const [id, detailId] = path.split("/");
  const view = VIEWS.find((entry) => entry.id === id);
  if (!view) return { kind: "unknown" };
  if (detailId === undefined) return { kind: "view", view };
  const detail = view.details?.find((entry) => entry.id === detailId);
  return detail ? { kind: "view", view, detail } : { kind: "unknown" };
};

/** The URL path of a view location: `pipeline`, or `pipeline/coverage` for its detail. */
export const locationPath = (location: {
  view: ViewSpec;
  detail?: DetailSpec;
}): ViewPath =>
  (location.detail
    ? `${location.view.id}/${location.detail.id}`
    : location.view.id) as ViewPath;
