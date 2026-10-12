import { describe, expect, test } from "bun:test";
import { readdir } from "node:fs/promises";
import { join } from "node:path";

/**
 * Guards the feature audit (docs/ux/feature-audit): every panel, figure and
 * section title recorded in the baseline must still be rendered somewhere in the
 * dashboard source. A title may move to another page or component; it may only
 * disappear when its entry names a decided change from the audit README.
 */
type Entry = {
  page: string;
  title: string;
  feature: string | null;
  removed: string | null;
};

const audit = join(import.meta.dir, "../../docs/ux/feature-audit");
const manifest: Entry[] = await Bun.file(join(audit, "titles.json")).json();
const decided = new Set(
  [
    ...(await Bun.file(join(audit, "README.md")).text()).matchAll(
      /^\| (DC\d+) \|/gm,
    ),
  ].map((match) => match[1]),
);

async function sourceText(directory: string): Promise<string> {
  const parts: string[] = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) parts.push(await sourceText(path));
    else if (/\.tsx?$/.test(entry.name) && !entry.name.includes(".test."))
      parts.push(await Bun.file(path).text());
  }
  return parts.join("\n");
}
const source = await sourceText(import.meta.dir);
/** The literal part of a title; template expressions vary with the data. */
const literal = (title: string) => title.split("${")[0]!.trim();

describe("feature audit baseline", () => {
  test("every audited title is still rendered, or removed by a decided change", () => {
    const missing = manifest
      .filter((entry) => entry.removed === null)
      .filter((entry) => !source.includes(literal(entry.title)))
      .map(
        (entry) =>
          `${entry.page}: ${entry.title} (${entry.feature ?? "no ID"})`,
      );
    expect(missing).toEqual([]);
  });
  test("a removal names a decision recorded in the audit README", () => {
    const undecided = manifest.filter(
      (entry) => entry.removed !== null && !decided.has(entry.removed),
    );
    expect(undecided).toEqual([]);
  });
});
