// Turns <dir>/<page>.checks.json (probe) and <dir>/<page>.inspect.json (scorer's yes/no answers to the
// `inspect` checks) into <dir>/<page>.scores.json by the rubric's scoring_rule, then runs score_rubric.py.
//
// Usage: bun scripts/ux-probe/score.ts <dir> <page...>
// inspect.json: { "A3": { "pass": false, "evidence": "<capture + position or file:line>" }, "D3": { ... } }
import { readFileSync } from "node:fs";

type Check = { pass: boolean; evidence: string };
type Rubric = {
  criteria: { id: string; checks: { id: string; level: number; method: string }[] }[];
};

const ROOT = new URL("../../", import.meta.url);
const RUBRIC = new URL("docs/ux/rubrics/ux-page.yaml", ROOT).pathname;
const SCORER = `${process.env.HOME}/.agents/by-request/setup-ux-standards/scripts/score_rubric.py`;
const rubric = Bun.YAML.parse(readFileSync(RUBRIC, "utf8")) as Rubric;
const readJson = <T>(path: string): T => JSON.parse(readFileSync(path, "utf8")) as T;

/** Highest level whose checks, and every lower level's, all pass. */
function criterionScore(checks: Rubric["criteria"][number]["checks"], results: Record<string, Check>) {
  let score = 0;
  for (const level of [1, 2, 3]) {
    const atLevel = checks.filter((c) => c.level === level);
    if (!atLevel.every((c) => results[c.id]!.pass)) break;
    score = level;
  }
  const rationale = checks
    .map((c) => `${c.id} ${results[c.id]!.pass ? "pass" : "FAIL"}: ${results[c.id]!.evidence}`)
    .join(" | ");
  return { score, rationale };
}

const [dir, ...pages] = process.argv.slice(2);
if (!dir || pages.length === 0) {
  console.error("usage: bun scripts/ux-probe/score.ts <dir> <page...>");
  process.exit(2);
}
for (const page of pages) {
  const name = page.replaceAll("/", "-");
  const probed = readJson<{ checks: Record<string, Check> }>(`${dir}/${name}.checks.json`);
  const inspected = readJson<Record<string, Check>>(`${dir}/${name}.inspect.json`);
  const results = { ...probed.checks, ...inspected };
  const missing = rubric.criteria.flatMap((c) => c.checks.map((k) => k.id)).filter((id) => !results[id]);
  if (missing.length) throw new Error(`${name}: no answer for ${missing.join(", ")}`);
  const scores = Object.fromEntries(rubric.criteria.map((c) => [c.id, criterionScore(c.checks, results)]));
  const out = `${dir}/${name}.scores.json`;
  await Bun.write(out, JSON.stringify({ page, scores }, null, 2));
  const run = Bun.spawnSync(["uv", "run", "--no-project", "--with", "pyyaml", "python", SCORER, RUBRIC, out]);
  if (run.exitCode !== 0) throw new Error(`${name}: score_rubric.py failed: ${run.stderr.toString()}`);
  const result = JSON.parse(run.stdout.toString()) as { total: number; band: string; criteria: { id: string; score: number }[] };
  await Bun.write(`${dir}/${name}.result.json`, JSON.stringify(result, null, 2));
  console.log(`${name}: ${result.total} ${result.band} — ${result.criteria.map((c) => `${c.id}=${c.score}`).join(" ")}`);
}
