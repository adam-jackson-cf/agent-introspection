import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const runtimePath = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "session-context-runtime.sh",
);

type LifecycleEvent = "session_start" | "session_end";
type NativeLifecycleEvent = "session_start" | "session_shutdown";

interface OmpExtensionContext {
  cwd: string;
  sessionManager: {
    getSessionId(): string;
  };
}

interface OmpExtensionAPI {
  on(
    event: NativeLifecycleEvent,
    handler: (event: unknown, context: OmpExtensionContext) => void,
  ): void;
}

function requireValue(value: unknown, name: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`OMP ${name} is required`);
  }

  return value;
}

function emitLifecycleEvent(context: OmpExtensionContext, eventType: LifecycleEvent): void {
  const sessionId = requireValue(context?.sessionManager?.getSessionId?.(), "session id");
  const workspace = requireValue(context?.cwd, "workspace");
  const occurredAt = new Date().toISOString();
  const result = spawnSync(
    runtimePath,
    ["omp", sessionId, eventType, occurredAt, workspace],
    { stdio: "inherit" },
  );

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`OMP session-context runtime exited with status ${result.status}`);
  }
}

export default function registerOmpLifecycleAdapter(api: OmpExtensionAPI): void {
  api.on("session_start", (_event, context) => {
    emitLifecycleEvent(context, "session_start");
  });
  api.on("session_shutdown", (_event, context) => {
    emitLifecycleEvent(context, "session_end");
  });
}
