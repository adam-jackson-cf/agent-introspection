import { useEffect, useRef, useState } from "react";
import type { Filters, ViewPath } from "./contracts";
import { useAppData, useRouting } from "./app-data";
import {
  FilterBar,
  LoadingSkeleton,
  Notices,
  PageBody,
  PageHead,
  RangeNotice,
  Sidebar,
  Topbar,
  ViewNav,
} from "./app-parts";
import { filterQuery, lastDays, presetFor, readFilters } from "./app-state";
import { enabledHarnesses } from "./harness-scope";
import { locationPath, type Location } from "./routes";

const locationTitle = (location: Location) => {
  if (location.kind === "view")
    return location.detail?.title ?? location.view.title;
  return location.kind === "session" ? "Session" : "Unknown view";
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

  const viewHref = (id: ViewPath, next: Filters = filters) =>
    `/${id}?${filterQuery(next)}`;
  const sessionHref = (harness: string, session: string) =>
    `/session?${new URLSearchParams({ ...Object.fromEntries(filterQuery(filters)), harness, session })}`;

  const title = locationTitle(location);
  useEffect(() => {
    document.title = `${title} · Agent introspection`;
    heading.current?.focus();
  }, [title]);

  const change = (next: Filters) => {
    if (location.kind === "view")
      navigate(viewHref(locationPath(location), next), next);
  };
  const setRange = (days: number) =>
    change({ ...filters, ...lastDays(days), range: days });
  // A live preset slides to now on Refresh; a custom range reloads as it is.
  const refresh = () => {
    if (filters.range === undefined || location.kind !== "view") {
      data.refresh();
      return;
    }
    const next = { ...filters, ...lastDays(filters.range) };
    data.reloadRegistry();
    navigate(viewHref(locationPath(location), next), next, true);
  };
  const harnesses = data.registry ? enabledHarnesses(data.registry) : [];
  // A link can name a harness this machine does not use; fall back to All.
  const stale =
    data.registry !== null &&
    filters.harness !== "" &&
    !harnesses.includes(filters.harness);
  useEffect(() => {
    if (stale && location.kind === "view")
      navigate(
        viewHref(locationPath(location), { ...filters, harness: "" }),
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
        <Topbar
          title={title}
          parent={
            location.kind === "view" && location.detail
              ? {
                  title: location.view.title,
                  href: viewHref(location.view.id),
                  onOpen: () => navigate(viewHref(location.view.id), filters),
                }
              : undefined
          }
          nav={nav}
          onRefresh={refresh}
        />
        <div id="content" className="content" tabIndex={-1}>
          <PageHead
            location={location}
            title={title}
            heading={heading}
            loading={data.loading}
            elapsedSeconds={data.elapsedSeconds}
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
          <Notices
            location={location}
            failure={data.failure}
            onRetry={refresh}
          />
          <RangeNotice response={viewResponse} />
          <div className={data.lastGoodView ? "stale-data" : undefined}>
            <PageBody
              location={location}
              registry={data.registry}
              filters={filters}
              sessionHref={sessionHref}
              viewHref={viewHref}
              navigate={navigate}
              viewResponse={viewResponse ?? data.lastGoodView}
              sessionResponse={sessionResponse}
            />
          </div>
          {data.awaiting && <LoadingSkeleton />}
        </div>
      </main>
    </div>
  );
}
