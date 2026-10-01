import { type ComponentType } from "react";
import { type Row, type ViewId } from "./contracts";
import { HEALTH_VIEWS } from "./routes-health";
import { OUTCOME_VIEWS } from "./routes-outcomes";

export type ViewSpec = {
  id: ViewId;
  number: string;
  title: string;
  question: string;
  component: ComponentType<{ data: Record<string, Row[]> }>;
};

/** Question-led views, in reading order. */
export const VIEWS: ViewSpec[] = [...HEALTH_VIEWS, ...OUTCOME_VIEWS];

export type Location =
  | { kind: "view"; view: ViewSpec }
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
  const view = VIEWS.find((entry) => entry.id === path);
  return view ? { kind: "view", view } : { kind: "unknown" };
};
