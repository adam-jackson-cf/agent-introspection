#!/usr/bin/env bun
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, relative, resolve } from "node:path";
import type TS from "typescript";

type Edge = { file: string; line: number; specifier: string; resolved: string | null };

const ts = createRequire(resolve(import.meta.dir, "../dashboard/package.json"))(
  "typescript",
) as typeof TS;

function compilerOptions(tsconfig: string): TS.CompilerOptions {
  const read = ts.readConfigFile(tsconfig, ts.sys.readFile);
  if (read.error !== undefined) {
    throw new Error(ts.flattenDiagnosticMessageText(read.error.messageText, " "));
  }
  const host = { ...ts.sys, readDirectory: (): string[] => [] };
  return ts.parseJsonConfigFileContent(read.config, host, dirname(tsconfig)).options;
}

function target(
  specifier: string,
  from: string,
  root: string,
  options: TS.CompilerOptions,
): string | null {
  const found = ts.resolveModuleName(specifier, from, options, ts.sys).resolvedModule;
  if (found === undefined || found.isExternalLibraryImport === true) return null;
  const path = relative(root, found.resolvedFileName);
  return path.startsWith("..") || path.includes("node_modules/") ? null : path;
}

function edges(root: string, file: string, options: TS.CompilerOptions): Edge[] {
  const absolute = resolve(root, file);
  const text = readFileSync(absolute, "utf8");
  const imports = ts.preProcessFile(text, true, true).importedFiles;
  return imports.map((item) => ({
    file,
    line: text.slice(0, item.pos).split("\n").length,
    specifier: item.fileName,
    resolved: target(item.fileName, absolute, root, options),
  }));
}

const args = process.argv.slice(2);
if (args[0] !== "--root" || args[1] === undefined || args[2] !== "--tsconfig" || !args[3]) {
  process.stderr.write("usage: list-typescript-imports.ts --root DIR --tsconfig FILE [FILE ...]\n");
  process.exit(2);
}
const root = resolve(args[1]);
const options = compilerOptions(resolve(root, args[3]));
const found = args.slice(4).flatMap((file) => edges(root, file, options));
process.stdout.write(`${JSON.stringify({ edges: found })}\n`);
