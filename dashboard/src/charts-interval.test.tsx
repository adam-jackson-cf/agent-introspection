import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import {
  axisTicks,
  IntervalChart,
  IntervalMarks,
  logExtent,
  logTicks,
  scaleValue,
  type IntervalGroup,
} from "./charts-interval";

const groups: IntervalGroup[] = [
  {
    key: "omp",
    label: "omp",
    color: "red",
    rows: [
      { key: "low", label: "low", n: 50, value: 80, lo: 70, hi: 90 },
      { key: "high", label: "high", n: 5, value: 60, lo: 30, hi: 85 },
    ],
  },
  {
    key: "exec",
    label: "Codex exec",
    color: "blue",
    rows: [
      { key: "low", label: "low", n: 12, value: null, lo: null, hi: null },
    ],
  },
];

const axis = { min: 0, max: 100, fixed: true };
const marks = (group: IntervalGroup, index: number) =>
  renderToStaticMarkup(
    <svg>
      <IntervalMarks
        slot={{ group, row: group.rows[index]!, y: 0 }}
        axis={axis}
        left={100}
        span={200}
        minN={20}
        format={(value) => `${value}%`}
      />
    </svg>,
  );

describe("IntervalMarks", () => {
  test("a dot is filled from the minimum sample and hollow below it", () => {
    expect(marks(groups[0]!, 0)).toContain('fill="red"');
    expect(marks(groups[0]!, 1)).toContain('fill="var(--color-surface)"');
    expect(marks(groups[0]!, 1)).toContain("high · n=5");
  });

  test("a row that cannot produce the measure reads not applicable, with no dot or whisker", () => {
    const html = marks(groups[1]!, 0);
    expect(html).toContain("not applicable");
    expect(html).not.toContain("interval-dot");
    expect(html).not.toContain("interval-whisker");
  });
});

describe("IntervalChart", () => {
  test("is one labelled tab stop", () => {
    const html = renderToStaticMarkup(
      <IntervalChart
        label="Clean"
        groups={groups}
        axis={axis}
        format={(value) => `${value}%`}
        minN={20}
      />,
    );
    expect(html).toContain('aria-label="Clean interval chart"');
    expect(html.match(/tabindex="0"/g)).toHaveLength(1);
  });
});

describe("interval axis", () => {
  test("a log axis ticks on decades and places them evenly", () => {
    expect(logTicks(1, 1000)).toEqual([1, 10, 100, 1000]);
    expect(logTicks(5, 500)).toEqual([10, 100]);
    const logAxis = { min: 1, max: 1000, log: true };
    expect(axisTicks(logAxis)).toEqual([1, 10, 100, 1000]);
    expect(scaleValue(logAxis, 10, 100, 300)).toBeCloseTo(200);
    expect(scaleValue(logAxis, 100, 100, 300)).toBeCloseTo(300);
  });

  test("the decade extent holds every value and a fixed axis ticks 0, 50, 100", () => {
    expect(logExtent([3, 450, 20])).toEqual({ min: 1, max: 1000 });
    expect(logExtent([])).toEqual({ min: 1, max: 10 });
    expect(axisTicks(axis)).toEqual([0, 50, 100]);
    expect(scaleValue(axis, 150, 0, 200)).toBe(200);
  });
});
