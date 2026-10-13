import { spawn } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

// Activity hooks for omp (see docs/hook-events.md). Each handler builds a small
// JSON envelope and hands it to the detached activity shim, which runs
// `agent-introspection hook omp <event>`. Handlers never block, throw, or change
// the agent's behaviour; a failed spawn loses one record and is logged by event and
// error code only. The error text of a failed tool result is handed to the shim in
// memory and reduced to a normalized signature there; it is never stored.
//
// omp exports no user-prompt event, so `before_agent_start` sends one: an
// `omp.user_prompt` OTLP log (the same shape as Claude Code's `user_prompt` and
// Codex's `codex.user_prompt`) to the collector omp itself exports to, taken from
// the standard `OTEL_EXPORTER_OTLP_*` variables. `facts sync` labels it with Jev.
// With no endpoint configured, omp exports nothing and neither does this. Prompt
// text leaves the machine only after the user opts in: the onboarding workflow sets
// `AGENT_INTROSPECTION_OMP_PROMPT_EXPORT=1` in `~/.omp/.env` at its prompt-export step.

const shimPath =
  process.env.AGENT_INTROSPECTION_SHIM ??
  join(
    homedir(),
    ".local/lib/agent-introspection/activity-hooks-v1/activity-shim.sh",
  );

const hooksLog = join(
  homedir(),
  ".local/share/agent-introspection/hooks.log",
);

type ActivityEvent =
  | "tool_call"
  | "tool_result"
  | "tool_approval_resolved"
  | "auto_retry_start"
  | "auto_retry_end"
  | "input";

interface OmpContext {
  cwd?: unknown;
  isIdle?(): boolean;
  sessionManager?: {
    getSessionId?(): unknown;
  };
}

interface ToolCallEvent {
  toolCallId?: unknown;
  toolName?: unknown;
  input?: unknown;
}

interface ToolResultEvent {
  toolCallId?: unknown;
  toolName?: unknown;
  isError?: unknown;
  content?: unknown;
  details?: { exitCode?: unknown };
}

interface BeforeAgentStartEvent {
  prompt?: unknown;
}

interface ToolApprovalResolvedEvent {
  toolCallId?: unknown;
  toolName?: unknown;
  approved?: unknown;
}

interface AutoRetryEvent {
  attempt?: unknown;
  success?: unknown;
}

interface InputEvent {
  text?: unknown;
}

type Handler<E> = (event: E, context: OmpContext) => void;

interface OmpExtensionAPI {
  on(event: "tool_call", handler: Handler<ToolCallEvent>): void;
  on(event: "tool_result", handler: Handler<ToolResultEvent>): void;
  on(
    event: "before_agent_start",
    handler: Handler<BeforeAgentStartEvent>,
  ): void;
  on(
    event: "tool_approval_resolved",
    handler: Handler<ToolApprovalResolvedEvent>,
  ): void;
  on(
    event: "auto_retry_start" | "auto_retry_end",
    handler: Handler<AutoRetryEvent>,
  ): void;
  on(event: "input", handler: Handler<InputEvent>): void;
}

function sessionId(context: OmpContext): string | undefined {
  const value = context?.sessionManager?.getSessionId?.();
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** Join the text parts of a tool result's content; other parts carry no text. */
function textOf(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((part) =>
      typeof part?.text === "string" ? (part.text as string) : "",
    )
    .join("\n");
}

function exitCodeOf(event: ToolResultEvent): number | undefined {
  const code = event.details?.exitCode;
  return typeof code === "number" && Number.isFinite(code) ? code : undefined;
}

/** Append one line naming the event and error code; never any payload. */
function logSpawnFailure(event: ActivityEvent, error: unknown): void {
  try {
    const code = (error as { code?: unknown })?.code;
    mkdirSync(dirname(hooksLog), { recursive: true });
    appendFileSync(
      hooksLog,
      `${new Date().toISOString()} omp ${event} spawn ${typeof code === "string" ? code : "error"}\n`,
    );
  } catch {
    // Logging must never disturb the session.
  }
}

function emit(
  event: ActivityEvent,
  context: OmpContext,
  fields: Record<string, unknown>,
): void {
  try {
    const session = sessionId(context);
    if (session === undefined || !existsSync(shimPath)) {
      return;
    }
    const cwd = typeof context.cwd === "string" ? context.cwd : "";
    const envelope = JSON.stringify({ session_id: session, cwd, ...fields });
    const child = spawn("/bin/sh", [shimPath, "omp", event], {
      detached: true,
      stdio: ["pipe", "ignore", "ignore"],
    });
    child.on("error", (error) => logSpawnFailure(event, error));
    child.stdin?.on("error", () => undefined);
    child.stdin?.end(envelope);
    child.unref();
  } catch {
    // Telemetry must never disturb the session.
  }
}

type OtlpValue = { stringValue: string } | { intValue: string };

function otlpLogsUrl(): string | undefined {
  const logs = process.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT;
  if (logs) return logs;
  const base = process.env.OTEL_EXPORTER_OTLP_ENDPOINT;
  return base ? `${base.replace(/\/+$/, "")}/v1/logs` : undefined;
}

function otlpHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const raw =
    process.env.OTEL_EXPORTER_OTLP_LOGS_HEADERS ??
    process.env.OTEL_EXPORTER_OTLP_HEADERS;
  for (const pair of (raw ?? "").split(",")) {
    const at = pair.indexOf("=");
    if (at > 0)
      headers[decodeURIComponent(pair.slice(0, at).trim())] =
        decodeURIComponent(pair.slice(at + 1).trim());
  }
  return headers;
}

function attribute(
  key: string,
  value: OtlpValue,
): { key: string; value: OtlpValue } {
  return { key, value };
}

/** Send one `omp.user_prompt` log to omp's OTLP collector; failures are dropped. */
function exportPrompt(prompt: unknown, context: OmpContext): void {
  try {
    if (process.env.AGENT_INTROSPECTION_OMP_PROMPT_EXPORT !== "1") {
      return;
    }
    const url = otlpLogsUrl();
    const session = sessionId(context);
    if (
      url === undefined ||
      session === undefined ||
      typeof prompt !== "string" ||
      prompt === ""
    ) {
      return;
    }
    const now = `${BigInt(Date.now()) * 1_000_000n}`;
    const body = {
      resourceLogs: [
        {
          resource: {
            attributes: [
              attribute("service.name", {
                stringValue: process.env.OTEL_SERVICE_NAME ?? "oh-my-pi",
              }),
            ],
          },
          scopeLogs: [
            {
              scope: { name: "agent-introspection.activity" },
              logRecords: [
                {
                  timeUnixNano: now,
                  observedTimeUnixNano: now,
                  severityText: "INFO",
                  body: { stringValue: "omp.user_prompt" },
                  attributes: [
                    attribute("event.name", { stringValue: "omp.user_prompt" }),
                    attribute("session.id", { stringValue: session }),
                    attribute("prompt", { stringValue: prompt }),
                    attribute("prompt_length", {
                      intValue: `${prompt.length}`,
                    }),
                  ],
                },
              ],
            },
          ],
        },
      ],
    };
    fetch(url, {
      method: "POST",
      headers: otlpHeaders(),
      body: JSON.stringify(body),
    }).catch(() => undefined);
  } catch {
    // Telemetry must never disturb the session.
  }
}

export default function registerOmpActivityHooks(api: OmpExtensionAPI): void {
  api.on("tool_call", (event, context) => {
    emit("tool_call", context, {
      tool_use_id: event.toolCallId,
      tool_name: event.toolName,
      tool_input: event.input,
    });
  });
  api.on("tool_result", (event, context) => {
    if (event.isError !== true) return;
    emit("tool_result", context, {
      tool_use_id: event.toolCallId,
      tool_name: event.toolName,
      error: textOf(event.content),
      exit_code: exitCodeOf(event),
    });
  });
  api.on("before_agent_start", (event, context) => {
    exportPrompt(event.prompt, context);
  });
  api.on("tool_approval_resolved", (event, context) => {
    emit("tool_approval_resolved", context, {
      tool_use_id: event.toolCallId,
      tool_name: event.toolName,
      approved: event.approved,
    });
  });
  api.on("auto_retry_start", (event, context) => {
    emit("auto_retry_start", context, { attempt: event.attempt });
  });
  api.on("auto_retry_end", (event, context) => {
    emit("auto_retry_end", context, {
      attempt: event.attempt,
      success: event.success,
    });
  });
  api.on("input", (event, context) => {
    // Only input typed while the agent is busy is a steer; idle input becomes a
    // prompt and is covered by before_agent_start.
    const idle = context?.isIdle?.() ?? true;
    if (!idle) {
      emit("input", context, { text: event.text, idle });
    }
  });
}
