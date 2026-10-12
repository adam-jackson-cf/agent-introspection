import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { DAY_KEYS, DailyChart, DataTable, sortRows } from "./charts";

const columns = [
  { key: "subject", label: "Subject" },
  { key: "task_types", label: "Task types" },
];

describe("DataTable", () => {
  test("an array cell, such as a finding's task types, reads as a comma list", () => {
    const html = renderToStaticMarkup(
      <DataTable
        columns={columns}
        rows={[{ subject: "a", task_types: ["feature", "bugfix"] }]}
      />,
    );
    expect(html).toContain("<td>feature, bugfix</td>");
  });
  test("a table cut by a limit says how many rows it shows", () => {
    const rows = [{ subject: "a" }, { subject: "b" }];
    expect(
      renderToStaticMarkup(
        <DataTable columns={columns} rows={rows} total={500} />,
      ),
    ).toContain("Showing 2 of 500");
    expect(
      renderToStaticMarkup(
        <DataTable columns={columns} rows={rows} total={2} />,
      ),
    ).not.toContain("Showing");
  });
  test("a limited query's total_rows sets the count unless a total is given", () => {
    const rows = [
      { subject: "a", total_rows: 46 },
      { subject: "b", total_rows: 46 },
    ];
    expect(
      renderToStaticMarkup(<DataTable columns={columns} rows={rows} />),
    ).toContain("Showing 2 of 46");
    expect(
      renderToStaticMarkup(
        <DataTable columns={columns} rows={rows} total={2} />,
      ),
    ).not.toContain("Showing");
  });
});

describe("sortRows", () => {
  const rows = [
    { harness: "omp", n: "9" },
    { harness: "", n: "100" },
    { harness: "claude-code", n: "10" },
    { harness: "codex", n: null },
  ];
  const order = (descending: boolean) =>
    sortRows(rows, { key: "n", descending }).map((row) => row.harness);

  test("number strings sort as numbers, and the All row keeps its place", () => {
    expect(order(false)).toEqual(["omp", "", "claude-code", "codex"]);
    expect(order(true)).toEqual(["claude-code", "", "omp", "codex"]);
  });

  test("no sort keeps the server's order", () => {
    expect(sortRows(rows, null)).toBe(rows);
  });
});

describe("DailyChart", () => {
  const rows = [
    { day: "2026-09-01", a: 1 },
    { day: "2026-09-03", a: 2 },
  ];
  const series = [{ key: "a", label: "A", color: "var(--color-primary)" }];
  test("is one tab stop: a labelled group with no focusable days", () => {
    const html = renderToStaticMarkup(
      <DailyChart
        rows={rows}
        series={series}
        kind="stack"
        format={(value) => String(value)}
      />,
    );
    expect(html.match(/tabindex="0"/g)).toHaveLength(1);
    expect(html).toContain('role="group"');
    expect(html).toContain("A daily chart");
  });
  test("arrows stop at the ends; Home and End jump; the first press lands on day 0", () => {
    expect(DAY_KEYS.ArrowRight!(null, 4)).toBe(0);
    expect(DAY_KEYS.ArrowRight!(3, 4)).toBe(4);
    expect(DAY_KEYS.ArrowRight!(4, 4)).toBe(4);
    expect(DAY_KEYS.ArrowLeft!(0, 4)).toBe(0);
    expect(DAY_KEYS.ArrowLeft!(3, 4)).toBe(2);
    expect(DAY_KEYS.Home!(3, 4)).toBe(0);
    expect(DAY_KEYS.End!(1, 4)).toBe(4);
  });
});
