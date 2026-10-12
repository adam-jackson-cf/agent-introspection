import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { filterQuery, lastDays } from "./app-state";
import { ViewContext, ViewLink } from "./components";
import type { Filters, Registry } from "./contracts";

const link = (filters: Filters) =>
  renderToStaticMarkup(
    <ViewContext.Provider
      value={{
        registry: {} as Registry,
        contributions: {},
        filters,
        minRateN: 0,
        sessionHref: () => "",
        viewHref: (id) => `/${id}?${filterQuery(filters)}`,
        navigate: () => {},
      }}
    >
      <ViewLink view="tools">Open Tool failures</ViewLink>
    </ViewContext.Provider>,
  );

describe("ViewLink", () => {
  test("keeps the preset range and the harness, and names its destination", () => {
    const html = link({ ...lastDays(30), range: 30, harness: "claude-code" });
    expect(html).toContain('href="/tools?range=30&amp;harness=claude-code"');
    expect(html).toContain("Open Tool failures");
  });
});
