#!/usr/bin/env bun
import { spawnSync } from "node:child_process";
import { builtinModules, createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { resolve, relative } from "node:path";
import type TS from "typescript";

type Node = TS.Node;

const MAX_LOOP_DEPTH = 2;
const MAX_CLASS_LINES = 700;
const LARGE_MODULE_NLOC = 800;
const MAX_LARGE_MODULE_FANOUT = 3;
const TRACKED_GLOBS = ["*.ts", "*.tsx", "*.mts", "*.cts"];
const PLACEHOLDER_MESSAGE = /not[ _-]?implemented|\btodo\b/i;
const ARRAY_ITERATORS: Record<string, true> = {
  every: true,
  filter: true,
  find: true,
  findIndex: true,
  findLast: true,
  findLastIndex: true,
  flatMap: true,
  forEach: true,
  map: true,
  reduce: true,
  reduceRight: true,
  some: true,
};

const args = process.argv.slice(2);
let root = process.cwd();
const explicitFiles: string[] = [];
for (let index = 0; index < args.length; index += 1) {
  if (args[index] === "--root") {
    root = resolve(args[index + 1] ?? ".");
    index += 1;
  } else {
    explicitFiles.push(args[index] as string);
  }
}

const ts = createRequire(resolve(root, "dashboard/package.json"))("typescript") as typeof TS;
const builtins = new Set(builtinModules.map((name) => name.replace(/^node:/, "")));

function isBuiltin(specifier: string): boolean {
  if (specifier.startsWith("node:") || specifier.startsWith("bun:") || specifier === "bun") {
    return true;
  }
  return builtins.has(specifier.split("/")[0] as string) && builtins.has(specifier);
}

function trackedFiles(): string[] {
  const listed = spawnSync("git", ["ls-files", "-z", "--", ...TRACKED_GLOBS], {
    cwd: root,
    encoding: "utf8",
    timeout: 30_000,
  });
  if (listed.status !== 0) {
    throw new Error(`git ls-files failed: ${listed.stderr}`);
  }
  return listed.stdout
    .split("\0")
    .filter((path) => path.length > 0 && !path.includes("node_modules/") && !path.includes("tests/fixtures/"));
}

interface Finding {
  file: string;
  line: number;
  rule: string;
  message: string;
}

function literalText(node: Node | undefined): string | undefined {
  return node !== undefined && ts.isStringLiteralLike(node) ? node.text : undefined;
}

function callSpecifier(node: TS.CallExpression): string | undefined {
  const callee = node.expression;
  const isLoader =
    callee.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(callee) && callee.text === "require");
  return isLoader && node.arguments.length === 1 ? literalText(node.arguments[0]) : undefined;
}

function importSpecifier(node: Node): string | undefined {
  if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
    return literalText(node.moduleSpecifier);
  }
  if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
    return literalText(node.moduleReference.expression);
  }
  return ts.isCallExpression(node) ? callSpecifier(node) : undefined;
}

function isLoop(node: Node): boolean {
  return (
    ts.isForStatement(node) ||
    ts.isForInStatement(node) ||
    ts.isForOfStatement(node) ||
    ts.isWhileStatement(node) ||
    ts.isDoStatement(node)
  );
}

function isIteratorCallback(node: Node): boolean {
  const call = node.parent;
  if (call === undefined || !ts.isCallExpression(call) || !call.arguments.includes(node as TS.Expression)) {
    return false;
  }
  return (
    ts.isPropertyAccessExpression(call.expression) && Object.hasOwn(ARRAY_ITERATORS, call.expression.name.text)
  );
}

function isDeclaredFunction(node: Node): node is TS.FunctionLikeDeclarationBase {
  if (!ts.isFunctionLike(node) || ts.isConstructorDeclaration(node)) {
    return false;
  }
  const isValue = ts.isArrowFunction(node) || ts.isFunctionExpression(node);
  return !isValue || (node.parent !== undefined && ts.isVariableDeclaration(node.parent));
}

function thrownMessage(statement: TS.Statement | undefined): string | undefined {
  if (statement === undefined || !ts.isThrowStatement(statement)) {
    return undefined;
  }
  const thrown = statement.expression;
  const isConstruction = ts.isNewExpression(thrown) || ts.isCallExpression(thrown);
  return literalText(isConstruction ? thrown.arguments?.[0] : thrown);
}

function placeholderKind(node: Node): string | undefined {
  const body = isDeclaredFunction(node) ? node.body : undefined;
  if (body === undefined || !ts.isBlock(body)) {
    return undefined;
  }
  if (body.statements.length === 0) {
    return "empty body";
  }
  const message = body.statements.length === 1 ? thrownMessage(body.statements[0]) : undefined;
  return message !== undefined && PLACEHOLDER_MESSAGE.test(message) ? "throws a not-implemented error" : undefined;
}

function nonBlankLines(text: string): number {
  return text.split("\n").filter((line) => {
    const stripped = line.trim();
    return stripped.length > 0 && !stripped.startsWith("//") && !stripped.startsWith("*") && !stripped.startsWith("/*");
  }).length;
}

function checkFile(path: string): Finding[] {
  const displayPath = relative(root, resolve(root, path));
  const text = readFileSync(resolve(root, path), "utf8");
  const languageName = path.replace(/\.fixture$/, "");
  const source = ts.createSourceFile(languageName, text, ts.ScriptTarget.Latest, true);
  const parseErrors = (source as unknown as { parseDiagnostics: unknown[] }).parseDiagnostics;
  if (parseErrors.length > 0) {
    return [{ file: displayPath, line: 1, rule: "TS.parse", message: "unparsable source fails closed" }];
  }

  const findings: Finding[] = [];
  const dependencies = new Set<string>();
  const lineOf = (node: Node) => source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;

  const placeholderFindings = (node: Node): Finding[] => {
    const kind = placeholderKind(node);
    const message = `function has only a placeholder body (${kind}); implement it, or declare it abstract, as an overload or with declare`;
    return kind === undefined ? [] : [{ file: displayPath, line: lineOf(node), rule: "CODE.no-placeholder-bodies", message }];
  };

  const visit = (node: Node, depth: number): void => {
    const specifier = importSpecifier(node);
    if (specifier !== undefined && !isBuiltin(specifier)) {
      dependencies.add(specifier);
    }
    if (ts.isClassLike(node)) {
      const lines = source.getLineAndCharacterOfPosition(node.end).line - lineOf(node) + 2;
      if (lines > MAX_CLASS_LINES) {
        findings.push({ file: displayPath, line: lineOf(node), rule: "TS.class-lines", message: `class spans ${lines} lines > ${MAX_CLASS_LINES}` });
      }
    }
    findings.push(...placeholderFindings(node));
    let nextDepth = depth;
    if (ts.isFunctionLike(node) && !isIteratorCallback(node)) {
      nextDepth = 0;
    } else if (isLoop(node) || isIteratorCallback(node)) {
      nextDepth = depth + 1;
      if (nextDepth === MAX_LOOP_DEPTH + 1) {
        findings.push({ file: displayPath, line: lineOf(node), rule: "TS.loop-depth", message: `nested loop depth ${nextDepth} > ${MAX_LOOP_DEPTH}` });
      }
    }
    ts.forEachChild(node, (child) => visit(child, nextDepth));
  };
  visit(source, 0);

  const nloc = nonBlankLines(text);
  if (nloc > LARGE_MODULE_NLOC && dependencies.size > MAX_LARGE_MODULE_FANOUT) {
    findings.push({ file: displayPath, line: 1, rule: "TS.fanout-large-module", message: `large module ${nloc} NLOC with non-builtin imports ${dependencies.size} > ${MAX_LARGE_MODULE_FANOUT}` });
  }
  return findings;
}

const files = explicitFiles.length > 0 ? explicitFiles : trackedFiles();
const all = files.flatMap((file) => checkFile(file));
for (const finding of all) {
  console.error(`${finding.file}:${finding.line}: ${finding.rule} ${finding.message}`);
}
console.error(`check-typescript-limits: ${files.length} files, ${all.length} findings`);
process.exit(all.length > 0 ? 1 : 0);
