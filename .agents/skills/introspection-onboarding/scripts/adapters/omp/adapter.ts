import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const runtimePath = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "session-context-runtime.sh",
);

type LifecycleEvent = "session_start" | "session_end";
type NativeLifecycleEvent = "session_start" | "session_shutdown";
const BOUNDED_REJECTION_STATUS = 65;

interface OmpExtensionContext {
  cwd: unknown;
  sessionManager?: {
    getSessionId?(): unknown;
  };
}

interface OmpCommandContext {
  ui: {
    notify(message: string, level: "info"): void;
  };
}

interface NativeEvent {
  timestamp?: unknown;
}

interface OmpExtensionAPI {
  on(
    event: NativeLifecycleEvent,
    handler: (event: NativeEvent, context: OmpExtensionContext) => void,
  ): void;
  registerCommand(
    name: string,
    command: {
      description: string;
      handler: (args: string, context: OmpCommandContext) => Promise<void>;
    },
  ): void;
}

function requireValue(value: unknown, name: string): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    throw new Error(`OMP ${name} is required and must not contain control characters`);
  }

  return value;
}

function nativeTimestamp(event: NativeEvent): string {
  if (event.timestamp === undefined) {
    return new Date().toISOString();
  }

  const timestamp = requireValue(event.timestamp, "timestamp");
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(
      timestamp,
    ) ||
    Number.isNaN(Date.parse(timestamp))
  ) {
    throw new Error("OMP timestamp must be RFC 3339");
  }
  return timestamp;
}

function emitLifecycleEvent(
  event: NativeEvent,
  context: OmpExtensionContext,
  eventType: LifecycleEvent,
): void {
  const sessionId = requireValue(context?.sessionManager?.getSessionId?.(), "session id");
  const workspace = requireValue(context?.cwd, "workspace");
  if (!workspace.startsWith("/")) {
    throw new Error("OMP workspace must be an absolute path");
  }
  const occurredAt = nativeTimestamp(event);
  const result = spawnSync(
    runtimePath,
    ["omp", sessionId, eventType, occurredAt, workspace],
    { stdio: "inherit" },
  );

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0 && result.status !== BOUNDED_REJECTION_STATUS) {
    throw new Error(`OMP session-context runtime exited with status ${result.status}`);
  }
}

export default function registerOmpLifecycleAdapter(api: OmpExtensionAPI): void {
  api.registerCommand("extension-health-agent-introspection", {
    description: "Verify agent-introspection extension registration",
    handler: async (_args, context) => {
      context.ui.notify("Extension registered: agent-introspection", "info");
    },
  });
  api.on("session_start", (event, context) => {
    emitLifecycleEvent(event, context, "session_start");
  });
  api.on("session_shutdown", (event, context) => {
    emitLifecycleEvent(event, context, "session_end");
  });
}
