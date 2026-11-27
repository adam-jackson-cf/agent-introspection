#!/usr/bin/env bun
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import type TS from "typescript";

type Case = { id: string; file: string; line: number; name: string; fingerprint: string };
type Unparsable = { file: string; error: string };

const ts = createRequire(resolve(import.meta.dir, "../dashboard/package.json"))(
  "typescript",
) as typeof TS;
const TEST_CALLEES = new Set(["test", "it"]);
const GROUP_CALLEES = new Set(["describe"]);

function calleeRoot(node: TS.Expression): string {
  if (ts.isIdentifier(node)) return node.text;
  if (ts.isPropertyAccessExpression(node)) return calleeRoot(node.expression);
  if (ts.isCallExpression(node)) return calleeRoot(node.expression);
  return "";
}

function title(node: TS.Expression | undefined, source: TS.SourceFile): string {
  if (node === undefined) return "";
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  return node.getText(source);
}

function declaredNames(body: TS.Node): Set<string> {
  const names = new Set<string>();
  const visit = (node: TS.Node): void => {
    const declared =
      ts.isVariableDeclaration(node) || ts.isParameter(node) || ts.isBindingElement(node);
    if (declared && ts.isIdentifier(node.name)) names.add(node.name.text);
    ts.forEachChild(node, visit);
  };
  visit(body);
  return names;
}

function canonical(node: TS.Node, source: TS.SourceFile, locals: Map<string, string>): string {
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, true, ts.LanguageVariant.Standard);
  scanner.setText(node.getText(source));
  const tokens: string[] = [];
  for (let kind = scanner.scan(); kind !== ts.SyntaxKind.EndOfFileToken; kind = scanner.scan()) {
    const text = scanner.getTokenText();
    tokens.push(kind === ts.SyntaxKind.Identifier ? (locals.get(text) ?? text) : text);
  }
  return tokens.join(" ");
}

function fingerprint(callback: TS.Node | undefined, source: TS.SourceFile): string {
  if (callback === undefined) return "";
  const locals = new Map<string, string>();
  for (const name of declaredNames(callback)) locals.set(name, `v${locals.size}`);
  const calls: string[] = [];
  const visit = (node: TS.Node): void => {
    if (ts.isCallExpression(node)) calls.push(canonical(node, source, locals));
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(callback, visit);
  calls.sort();
  return createHash("sha256").update(JSON.stringify(calls)).digest("hex").slice(0, 20);
}

function collect(file: string, source: TS.SourceFile): Case[] {
  const cases: Case[] = [];
  const visit = (node: TS.Node, groups: string[]): void => {
    if (ts.isCallExpression(node)) {
      const root = calleeRoot(node.expression);
      const name = title(node.arguments[0], source);
      if (GROUP_CALLEES.has(root)) {
        for (const argument of node.arguments.slice(1)) visit(argument, [...groups, name]);
        return;
      }
      if (TEST_CALLEES.has(root)) {
        const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
        const id = `${file}::${[...groups, name].join(" > ")}`;
        cases.push({ id, file, line, name, fingerprint: fingerprint(node.arguments[1], source) });
        return;
      }
    }
    ts.forEachChild(node, (child) => visit(child, groups));
  };
  visit(source, []);
  return cases;
}

const args = process.argv.slice(2);
if (args[0] !== "--root" || args[1] === undefined) {
  process.stderr.write("usage: list-bun-test-cases.ts --root DIR [FILE ...]\n");
  process.exit(2);
}
const root = args[1];
const files = args.slice(2);
const cases: Case[] = [];
const unparsable: Unparsable[] = [];
for (const file of files) {
  const text = readFileSync(resolve(root, file), "utf8");
  const kind = file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const errors = (source as unknown as { parseDiagnostics: TS.Diagnostic[] }).parseDiagnostics;
  if (errors.length > 0) {
    unparsable.push({ file, error: ts.flattenDiagnosticMessageText(errors[0]?.messageText ?? "", " ") });
  } else {
    cases.push(...collect(file, source));
  }
}
process.stdout.write(`${JSON.stringify({ cases, unparsable })}\n`);
