import { expect, test } from "bun:test";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { swapDist } from "./swap-dist";

function tmp() {
  return mkdtempSync(join(tmpdir(), "swap-dist-"));
}

test("failed swap keeps the last good dist", async () => {
  const root = tmp();
  const dist = join(root, "dist");
  mkdirSync(dist);
  writeFileSync(join(dist, "index.html"), "good");
  await expect(swapDist(join(root, "missing-stage"), dist)).rejects.toThrow();
  expect(readFileSync(join(dist, "index.html"), "utf8")).toBe("good");
  expect(existsSync(`${dist}.bak`)).toBe(false);
});

test("successful swap replaces dist and leaves no backup", async () => {
  const root = tmp();
  const dist = join(root, "dist");
  const stage = join(root, "stage");
  mkdirSync(dist);
  mkdirSync(stage);
  writeFileSync(join(dist, "index.html"), "old");
  writeFileSync(join(stage, "index.html"), "new");
  await swapDist(stage, dist);
  expect(readFileSync(join(dist, "index.html"), "utf8")).toBe("new");
  expect(existsSync(`${dist}.bak`)).toBe(false);
  expect(existsSync(stage)).toBe(false);
});
