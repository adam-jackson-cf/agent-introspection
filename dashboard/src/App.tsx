import { useEffect, useRef, useState } from "react";
import type { Filters, ViewId } from "./contracts";
import { useAppData, useRouting } from "./app-data";
import {
  FilterBar,
  Notices,
  PageBody,
  PageHead,
  Sidebar,
  Topbar,
  ViewNav,
} from "./app-parts";
import { filterQuery, lastDays, presetFor, readFilters } from "./app-state";
import { enabledHarnesses } from "./harness-scope";

const locationTitle = (kind: string, viewTitle: string) => {
  if (kind === "view") return viewTitle;
  return kind === "session" ? "Session" : "Unknown view";
};

export default function App() {
  const { location, filters, navigate } = useRouting();
  const [custom, setCustom] = useState(
    () => presetFor(readFilters()) === "Custom range",
  );
  // History restores filters without touching the selector; re-derive it.
  useEffect(() => {
    setCustom(presetFor(filters) === "Custom range");
  }, [filters]);
  const data = useAppData(location, filters);
  const heading = useRef<HTMLHeadingElement>(null);

  const viewHref = (id: ViewId, next: Filters = filters) =>
    `/${id}?${filterQuery(next)}`;
  const sessionHref = (harness: string, session: string) =>
    `/session?${new URLSearchParams({ ...Object.fromEntries(filterQuery(filters)), harness, session })}`;

  const title = locationTitle(
    location.kind,
    location.kind === "view" ? location.view.title : "",
  );
  useEffect(() => {
    document.title = `${title} · Agent introspection`;
    heading.current?.focus();
  }, [title]);

  const change = (next: Filters) => {
    if (location.kind === "view")
      navigate(viewHref(location.view.id, next), next);
  };
  const setRange = (days: number) => change({ ...filters, ...lastDays(days) });
  const harnesses = data.registry ? enabledHarnesses(data.registry) : [];
  // A link can name a harness this machine does not use; fall back to All.
  const stale =
    data.registry !== null &&
    filters.harness !== "" &&
    !harnesses.includes(filters.harness);
  useEffect(() => {
    if (stale && location.kind === "view")
      navigate(
        viewHref(location.view.id, { ...filters, harness: "" }),
        { ...filters, harness: "" },
        true,
      );
  });

  const nav = (
    <ViewNav
      location={location}
      filters={filters}
      viewHref={viewHref}
      navigate={navigate}
    />
  );
  const { viewResponse, sessionResponse } = data;

  return (
    <div className="app">
      <a className="skip" href="#content">
        Skip to content
      </a>
      <Sidebar nav={nav} />
      <main className="main">
        <Topbar title={title} nav={nav} onRefresh={() => data.refresh()} />
        <div id="content" className="content" tabIndex={-1}>
          <PageHead
            location={location}
            title={title}
            heading={heading}
            loading={data.loading}
            failure={data.failure}
            viewResponse={viewResponse}
            sessionResponse={sessionResponse}
          />
          {location.kind === "view" && (
            <FilterBar
              filters={filters}
              harnesses={harnesses}
              custom={custom}
              setCustom={setCustom}
              setRange={setRange}
              change={change}
            />
          )}
          <Notices location={location} failure={data.failure} />
          <PageBody
            location={location}
            registry={data.registry}
            filters={filters}
            sessionHref={sessionHref}
            viewResponse={viewResponse}
            sessionResponse={sessionResponse}
          />
          {data.loading &&
            !viewResponse &&
            !sessionResponse &&
            !data.failure && (
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
