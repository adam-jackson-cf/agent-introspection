// Answers every `auto` check in docs/ux/rubrics/ux-page.yaml for each page and writes <outDir>/<page>.checks.json
// plus <page>-first.png and <page>-full.png. The dashboard must be running on 127.0.0.1:4173.
//
// Usage: bun scripts/ux-probe/probe.ts <outDir> <page...>
//   page: pipeline, pipeline/coverage, cache, effort, tools, friction, guardrails, provider, recurrence,
//         intent, interventions, session (the first loop session from Tools; its ID is never printed).
import { readFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import puppeteer from "puppeteer-core";

const ROOT = new URL("../../", import.meta.url);
const BASE = "http://127.0.0.1:4173";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const VIEWPORT = { width: 2560, height: 1330 };

type Check = { pass: boolean; evidence: string };
type Rubric = {
  version: number;
  definitions: Record<string, string> & { state_words: string[] };
  criteria: { checks: { id: string; method: "auto" | "inspect"; views_only?: boolean }[] }[];
};
const rubric = Bun.YAML.parse(
  readFileSync(new URL("docs/ux/rubrics/ux-page.yaml", ROOT), "utf8"),
) as Rubric;
const pattern = (key: string) => new RegExp(rubric.definitions[key]!.trim(), "g");

/** View files whose source the S and C checks read, per page. */
const SOURCES: Record<string, string[]> = {
  pipeline: ["Pipeline.tsx"],
  "pipeline/coverage": ["PipelineCoverage.tsx"],
  cache: ["Cache.tsx"],
  effort: ["Effort.tsx"],
  tools: ["Tools.tsx"],
  friction: ["Friction.tsx"],
  guardrails: ["Guardrails.tsx"],
  provider: ["Provider.tsx"],
  recurrence: ["Recurrence.tsx"],
  intent: ["Intent.tsx"],
  interventions: ["Interventions.tsx"],
  session: ["Session.tsx"],
};

function sourceChecks(page: string): Record<string, Check> {
  const files = SOURCES[page];
  if (!files) throw new Error(`unknown page ${page}`);
  const count = (key: string) =>
    files.reduce((total, file) => {
      const text = readFileSync(new URL(`dashboard/src/views/${file}`, ROOT), "utf8");
      return total + (text.match(pattern(key))?.length ?? 0);
    }, 0);
  const computations = count("computation_pattern");
  const colours = count("colour_literal_pattern");
  const evidence = `${computations} computation_pattern matches in ${files.join(", ")}`;
  return {
    C1: { pass: colours === 0, evidence: `${colours} colour literals in ${files.join(", ")}` },
    S1: { pass: computations <= 3, evidence },
    S2: { pass: computations <= 1, evidence },
    S3: { pass: computations === 0, evidence },
  };
}

/**
 * Runs in the page: every DOM-measured auto check. Self-contained (serialised into the browser), so the
 * check groups are nested functions sharing the measurements taken here.
 */
function domChecks(args: { period: string; states: string[]; session: boolean; fold: number }) {
  const period = new RegExp(args.period, "i");
  const root = document.querySelector<HTMLElement>(".content")!;
  const top = (el: Element) => el.getBoundingClientRect().top + window.scrollY;
  const bottom = (el: Element) => el.getBoundingClientRect().bottom + window.scrollY;
  const text = (el: Element | null | undefined) => el?.textContent?.trim() ?? "";
  const visible = (el: Element) => el.getBoundingClientRect().height > 0;
  const sections = [...root.querySelectorAll(".view-section")];
  const articles = [...root.querySelectorAll<HTMLElement>("article.panel")];
  const kpis = articles.filter((a) => a.querySelector(".kpi"));
  const panels = articles.filter((a) => !a.querySelector(".kpi"));
  const headed = panels; // a panel built without the shared header fails the header checks, never skips them
  const height = document.documentElement.scrollHeight;
  const views = !args.session;
  const result: Record<string, { pass: boolean; evidence: string }> = {};
  const set = (id: string, pass: boolean, evidence: string) => (result[id] = { pass, evidence });
  const titleOf = (a: Element) => text(a.querySelector("h3")) || "(untitled panel)";
  const failing = (list: Element[]) => list.map(titleOf).join("; ") || "none";
  const first = sections[0] as Element | undefined;
  const head = root.querySelector(".head");
  const subtitle = (a: Element) => text(a.querySelector(".panel-head > div > p"));

  function zoom() {
    set("Z1", !!first && bottom(first) <= args.fold, `first section ends at ${first ? Math.round(bottom(first)) : "none"} px`);
    const open = root.querySelectorAll("details[open]").length;
    set("Z2", open === 0 && height <= 3 * args.fold, `${open} open details; page ${height} px`);
    const second = sections[1]?.querySelector("h2");
    set("Z3", height <= 2 * args.fold && !!second && top(second) <= args.fold, `page ${height} px; second heading at ${second ? Math.round(top(second)) : "none"} px`);
  }

  function numbers() {
    const detail = (a: Element) => text(a.querySelector(".kpi > span"));
    const noDetail = kpis.filter((a) => !detail(a));
    set("N1", !views || noDetail.length === 0, `${kpis.length} KPIs; without detail: ${failing(noDetail)}`);
    const noPeriod = kpis.filter((a) => !period.test(detail(a)));
    set("N2", !views || noPeriod.length === 0, `KPIs without a period: ${failing(noPeriod)}`);
    const unperiodic = headed.filter((a) => !period.test(subtitle(a)));
    const live = text(root.querySelector(".live"));
    const asOf = live.includes(views ? "Data as of" : "Queried");
    set("N3", unperiodic.length === 0 && asOf, `panels without a period: ${failing(unperiodic)}; as-of shown: ${asOf}`);
  }

  function layers() {
    const firstPanels = first ? first.querySelectorAll("article.panel").length : 0;
    const firstCharts = first ? first.querySelectorAll(".chart").length : 0;
    set("L1", !views || (!!first && firstPanels <= 5 && firstCharts === 0), `first section: ${firstPanels} panels, ${firstCharts} charts`);
    const twinChips = sections.filter((s) => {
      const rows = [...s.querySelectorAll(".contributors")].map(text);
      return new Set(rows).size < rows.length;
    });
    set("L2", twinChips.length === 0, `sections repeating a chip row: ${twinChips.map((s) => text(s.querySelector("h2"))).join("; ") || "none"}`);
    // Navigation links only: per-row links inside tables (sessions, view names in a list) are row identifiers,
    // and their text is never printed, so no session ID reaches the evidence.
    const links = [...root.querySelectorAll("a.view-link")].filter((a) => !a.closest("td")).map((a) => `${text(a)} → ${a.getAttribute("href")}`);
    const lines = [...root.querySelectorAll(".panel-head > div > p, .kpi > span")].map(text).filter(Boolean);
    const dupes = (list: string[]) => [...new Set(list.filter((v, i) => list.indexOf(v) !== i))];
    const twiceLinks = dupes(links).map((l) => l.split(" → ")[0]);
    const twiceLines = dupes(lines);
    set("L3", twiceLinks.length === 0 && twiceLines.length === 0, `repeated links: ${twiceLinks.join("; ") || "none"}; repeated lines: ${twiceLines.length}`);
  }

  function consistency() {
    const titles = root.querySelectorAll("[title]").length;
    const loose = [...root.querySelectorAll("table")].filter((t) => !t.closest(".table-wrap, .panel-popover")).length;
    set("C2", titles === 0 && loose === 0, `${titles} title attributes; ${loose} tables outside .table-wrap`);
    const words = [...root.querySelectorAll(".state-chip")].map((chip) => {
      const copy = chip.cloneNode(true) as Element;
      copy.querySelectorAll("b").forEach((b) => b.remove());
      return text(copy);
    });
    const odd = [...new Set(words.filter((w) => !args.states.includes(w)))];
    set("C3", odd.length === 0, `${words.length} state chips; other words: ${odd.join("; ") || "none"}`);
  }

  /** Rows of panels that sit side by side in one grid, keyed by their top edge. */
  function gridRows(grid: HTMLElement) {
    const rows = new Map<number, HTMLElement[]>();
    for (const panel of grid.querySelectorAll<HTMLElement>(":scope > article.panel")) {
      const key = Math.round(top(panel));
      rows.set(key, [...(rows.get(key) ?? []), panel]);
    }
    return [...rows.values()];
  }

  /** A row is filled when its spans add up to the grid's columns and its panels share a height. */
  function rowFilled(row: HTMLElement[], columns: number) {
    const heights = row.map((p) => Math.round(p.getBoundingClientRect().height));
    const spans = row.reduce((sum, p) => sum + Number(p.dataset.span ?? 6), 0);
    return Math.max(...heights) - Math.min(...heights) <= 2 && spans === columns;
  }

  function emptyBelow(a: HTMLElement) {
    const inner = [...a.querySelectorAll(":scope *")].filter((el) => visible(el) && !el.closest(".panel-popover"));
    const last = Math.max(...inner.map(bottom), top(a));
    const pad = parseFloat(getComputedStyle(a).paddingBottom);
    return Math.round(bottom(a) - pad - last);
  }

  function fit() {
    const sidebar = document.querySelector(".sidebar")?.getBoundingClientRect().right ?? 0;
    const gap = Math.round(root.getBoundingClientRect().left - sidebar);
    const overflow = document.documentElement.scrollWidth > window.innerWidth;
    set("F1", !overflow && gap <= 40, `horizontal overflow: ${overflow}; gap from sidebar ${gap} px`);
    const badRows = [...root.querySelectorAll<HTMLElement>(".grid")].flatMap((grid) =>
      gridRows(grid).filter((row) => !rowFilled(row, Number(grid.dataset.columns ?? 12))),
    );
    set("F2", badRows.length === 0, `rows not filled or unequal: ${badRows.map((row) => row.map(titleOf).join(" + ")).join("; ") || "none"}`);
    const slack = articles.map((a) => ({ a, empty: emptyBelow(a) })).filter((s) => s.empty > 160);
    set("F3", slack.length === 0, `panels with >160 px empty: ${slack.map((s) => `${titleOf(s.a)} (${s.empty} px)`).join("; ") || "none"}`);
  }

  function terminology() {
    const noInfo = articles.filter((a) => !a.querySelector(".panel-info"));
    set("T1", noInfo.length === 0, `panels without an i marker: ${failing(noInfo)}`);
    const emptyNote = articles.filter((a) => !text(a.querySelector(".panel-popover")));
    set("T2", noInfo.length === 0 && emptyNote.length === 0, `empty popovers: ${failing(emptyNote)}`);
    const noFormula = headed.filter((a) => !text(a.querySelector(".panel-popover .formula")));
    set("T3", noFormula.length === 0, `panels without a registry formula: ${failing(noFormula)}`);
  }

  function structure() {
    const headOk = !!text(head?.querySelector("h1")) && !!text(head?.querySelector("p"));
    const filters = !!root.querySelector(".filters");
    set("P1", headOk && (!views || filters), `title and question: ${headOk}; filter bar: ${filters}`);
    const firstTitle = text(first?.querySelector("h2"));
    set("P2", !views || firstTitle === "At a glance", `first section: "${firstTitle}"`);
    const outside = articles.filter((a) => !a.closest(".view-section"));
    set("P3", outside.length === 0, `${outside.length} panels outside a section`);
  }

  function navigation() {
    const current = new Set([...document.querySelectorAll(".sidebar a[aria-current='page']")].map((a) => a.getAttribute("href")));
    set("V1", !views || current.size === 1, `${current.size} current sidebar items`);
    const viewLinks = root.querySelectorAll("a.view-link").length;
    set("V2", viewLinks > 0, `${viewLinks} point-of-need links`);
    const unlinked = kpis.filter((a) => !a.querySelector("a[href]"));
    set("V3", !views || unlinked.length === 0, `KPIs without a link: ${failing(unlinked)}`);
  }

  function analysis() {
    const question = text(head?.querySelector("p"));
    const firstHasData = !!first?.querySelector(".kpi, table, .chart");
    set("A1", !!question && firstHasData, `question shown: ${!!question}; first section has a figure or table: ${firstHasData}`);
    const noSubtitle = headed.filter((a) => !subtitle(a));
    set("A2", noSubtitle.length === 0, `panels without a subtitle: ${failing(noSubtitle)}`);
  }

  function smallText(el: Element) {
    const ownText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent!.trim());
    return ownText && parseFloat(getComputedStyle(el).fontSize) < 12 && (el as HTMLElement).offsetParent !== null;
  }

  function trackedCaps(el: Element) {
    const style = getComputedStyle(el);
    return style.textTransform === "uppercase" && parseFloat(style.letterSpacing) > 0.5 && !!text(el) && visible(el);
  }

  function readability() {
    const small = [...document.querySelectorAll("body *")].filter(smallText).length;
    set("R1", small <= 10, `${small} visible text elements below 12 px`);
    result.R2 = { pass: small === 0, evidence: `${small} below 12 px` }; // contrast added by the caller from axe
    const tracked = [...root.querySelectorAll("*")].filter(trackedCaps).length;
    const proportional = [...root.querySelectorAll("td.num")].filter((td) => !getComputedStyle(td).fontVariantNumeric.includes("tabular-nums")).length;
    set("R3", tracked === 0 && proportional === 0, `${tracked} tracked-capital elements; ${proportional} numeric cells without tabular figures`);
  }

  /** A chart is sized and keyed when its plot is 280–360 px high and a legend or chip row names its series. */
  function chartOk(c: Element) {
    const svg = c.querySelector("svg");
    const h = svg ? Math.round(svg.getBoundingClientRect().height) : 0;
    const panel = c.closest("article.panel");
    const keyed = !!panel?.querySelector(".legend, .contributors") || !!c.closest(".view-section")?.querySelector(":scope > .contributors");
    return h >= 280 && h <= 360 && keyed;
  }

  function charts() {
    const all = [...root.querySelectorAll(".chart[role='group']")];
    const noTable = all.filter((c) => {
      const summaries = c.closest("article.panel")?.querySelectorAll(".table-view summary") ?? [];
      return ![...summaries].some((s) => text(s) === "View as table");
    });
    set("D1", noTable.length === 0, `${all.length} charts; without "View as table": ${noTable.length}`);
    const badCharts = all.filter((c) => !chartOk(c));
    set("D2", badCharts.length === 0, `charts off 280–360 px or unkeyed: ${badCharts.length}`);
  }

  zoom();
  numbers();
  layers();
  consistency();
  fit();
  terminology();
  structure();
  navigation();
  analysis();
  readability();
  charts();
  const failed = !!root.querySelector(".state-notice[role='alert']");
  return { checks: result, pageHeight: height, title: text(root.querySelector("h1")), path: location.pathname, failed };
}

async function sessionTarget(): Promise<string> {
  const end = new Date();
  const start = new Date(end.getTime() - 7 * 86_400_000);
  const query = `view=tools&start=${start.toISOString()}&end=${end.toISOString()}`;
  const body = (await (await fetch(`${BASE}/api/view?${query}`)).json()) as {
    data?: { loops?: { harness: string; session_id: string }[] };
  };
  const loop = body.data?.loops?.[0];
  if (!loop) throw new Error("no Tools loop session in the last 7 days to probe Session with");
  return `session?harness=${encodeURIComponent(loop.harness)}&session=${encodeURIComponent(loop.session_id)}`;
}

const [outDir, ...pages] = process.argv.slice(2);
if (!outDir || pages.length === 0) {
  console.error("usage: bun scripts/ux-probe/probe.ts <outDir> <page...>");
  process.exit(2);
}
await mkdir(outDir, { recursive: true });
const axeSource = readFileSync(new URL("node_modules/axe-core/axe.min.js", import.meta.url), "utf8");
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, protocolTimeout: 180_000, defaultViewport: VIEWPORT });
const tab = await browser.newPage();
await tab.setBypassCSP(true);
const autoIds = rubric.criteria.flatMap((c) => c.checks.filter((k) => k.method === "auto").map((k) => k.id));
const inspectIds = rubric.criteria.flatMap((c) => c.checks.filter((k) => k.method === "inspect").map((k) => k.id));
for (const page of pages) {
  const session = page === "session";
  const path = session ? await sessionTarget() : `${page}?range=7`;
  const name = page.replaceAll("/", "-");
  await tab.goto(`${BASE}/${path}`, { waitUntil: "networkidle0", timeout: 180_000 });
  // "Query failed" ends the wait too, so the dom.failed check below reports it instead of a 180 s timeout.
  await tab.waitForFunction(() => /Queried|Query failed/.test(document.body.innerText) && !document.querySelector(".skeleton"), { timeout: 180_000 });
  await Bun.sleep(500);
  await tab.screenshot({ path: `${outDir}/${name}-first.png` });
  await tab.screenshot({ path: `${outDir}/${name}-full.png`, fullPage: true });
  const dom = await tab.evaluate(domChecks, {
    period: rubric.definitions.period_phrase!.trim(),
    states: rubric.definitions.state_words,
    session,
    fold: VIEWPORT.height,
  });
  await tab.addScriptTag({ content: axeSource });
  const contrast = await tab.evaluate(async () => {
    // @ts-expect-error axe is injected above
    const run = await axe.run(document, { runOnly: ["color-contrast"] });
    return (run.violations as { nodes: unknown[] }[]).reduce((n, v) => n + v.nodes.length, 0);
  });
  const checks: Record<string, Check> = { ...dom.checks, ...sourceChecks(page) };
  checks.R2 = { pass: checks.R2!.pass && contrast === 0, evidence: `${checks.R2!.evidence}; ${contrast} contrast failures` };
  const missing = autoIds.filter((id) => !checks[id]);
  if (missing.length) throw new Error(`probe answered no result for ${missing.join(", ")}`);
  const inspect = Object.fromEntries(inspectIds.map((id) => [id, null]));
  await Bun.write(`${outDir}/${name}.checks.json`, JSON.stringify({ page, rubricVersion: rubric.version, title: dom.title, path: dom.path, pageHeight: dom.pageHeight, checks, inspect }, null, 2));
  const failed = autoIds.filter((id) => !checks[id]!.pass);
  console.log(`${name} [${dom.path} "${dom.title}"]: ${autoIds.length - failed.length}/${autoIds.length} auto checks pass; failing ${failed.join(", ") || "none"}`);
  if (dom.failed) throw new Error(`${name}: the page shows "Query failed"; its checks above are not valid, re-run when ClickHouse answers`);
}
await browser.close();
