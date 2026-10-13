import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { DailyChart, PanelContext, seriesAreHarnesses } from "./charts";
import { Contributors, ViewContext, type ViewContextValue } from "./components";
import type { Registry } from "./contracts";

const registry = {
  signals: [{ signal: "friction.interrupt", scope: "harness" }],
  harnesses: [
    { harness: "oh-my-pi", label: "omp", enabled: 1 },
    { harness: "claude-code", label: "Claude Code", enabled: 1 },
  ],
  support: [],
  hidden: [],
} as unknown as Registry;

const view: ViewContextValue = {
  registry,
  contributions: {
    "friction.interrupt": {
      "oh-my-pi": { state: "rows", rows: 12, from: null },
      "claude-code": { state: "no events", rows: 0, from: null },
    },
  },
  filters: { start: "", end: "", harness: "" },
  minRateN: 0,
  sessionHref: () => "",
  viewHref: () => "",
  navigate: () => {},
  errors: {},
};

const chips = (neutral: boolean) =>
  renderToStaticMarkup(
    <ViewContext.Provider value={view}>
      <Contributors signals={["friction.interrupt"]} neutral={neutral} />
    </ViewContext.Provider>,
  );

describe("contributor chips", () => {
  test("harness charts keep coloured chips that act as the legend", () => {
    const html = chips(false);
    expect(html).toContain("<i ");
    expect(html).not.toContain("Data from");
  });

  test("a chart of parts gets neutral source status with no colour swatch", () => {
    const html = chips(true);
    expect(html).toContain("Data from");
    expect(html).toContain("omp");
    expect(html).toContain("no events");
    expect(html).not.toContain("<i ");
  });
});

describe("chart legend beside chips", () => {
  const rows = [{ day: "2026-09-01", a: 1 }];
  const chart = (series: { key: string; label: string; color: string }[]) =>
    renderToStaticMarkup(
      <PanelContext.Provider
        value={{ label: "P", harnessChips: true, reportSeries: () => {} }}
      >
        <DailyChart
          rows={rows}
          series={series}
          kind="line"
          format={(value) => String(value)}
        />
      </PanelContext.Provider>,
    );
  const harnesses = [
    { key: "oh-my-pi", label: "omp", color: "red" },
    { key: "claude-code", label: "Claude Code", color: "blue" },
  ];
  const parts = [
    { key: "interrupt", label: "Interrupt", color: "green" },
    { key: "steer", label: "Steer", color: "gold" },
  ];

  test("harness series lean on the chips; other series carry their own legend", () => {
    expect(seriesAreHarnesses(harnesses)).toBe(true);
    expect(seriesAreHarnesses(parts)).toBe(false);
    expect(chart(harnesses)).not.toContain('class="legend"');
    expect(chart(parts)).toContain('class="legend"');
  });
});
