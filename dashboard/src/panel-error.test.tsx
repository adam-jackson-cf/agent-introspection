import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { Registry } from "./contracts";
import { failedSources, Panel, ViewContext } from "./components";

const render = (errors: Record<string, string>, source: string | string[]) =>
  renderToStaticMarkup(
    <ViewContext.Provider
      value={{
        registry: {} as Registry,
        contributions: {},
        filters: { start: "", end: "", harness: "" },
        minRateN: 0,
        sessionHref: () => "",
        viewHref: () => "",
        navigate: () => {},
        errors,
      }}
    >
      <Panel title="Latency" signals={[]} source={source}>
        <p>panel body</p>
      </Panel>
    </ViewContext.Provider>,
  );

describe("Panel error state", () => {
  test("a panel whose dataset failed shows the failure instead of its body", () => {
    const html = render({ latency: "Code: 241. Memory limit" }, "latency");
    expect(html).toContain('role="alert"');
    expect(html).toContain("Code: 241. Memory limit");
    expect(html).not.toContain("panel body");
  });

  test("panels on other datasets, or with no source, render normally", () => {
    expect(render({ latency: "failed" }, "daily")).toContain("panel body");
    expect(render({ latency: "failed" }, [])).toContain("panel body");
  });

  test("any failed dataset of several fails the panel, each message once", () => {
    expect(
      failedSources({ a: "x", b: "x", c: "y" }, ["a", "b", "missing"]),
    ).toEqual(["x", "x"]);
    expect(render({ b: "x" }, ["a", "b"])).not.toContain("panel body");
  });
});
