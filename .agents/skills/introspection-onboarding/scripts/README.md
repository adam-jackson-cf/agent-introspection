# Session-context capture

`session-context-runtime.sh` is the sole shared project-identity and canonical-record runtime. Surface integrations live in `adapters/<producer>/`; they extract only authoritative native metadata and then invoke this runtime.

## Layout

```text
scripts/
├── session-context-runtime.sh        # canonical validation, Git resolution, and inbox write
├── README.md                          # shared contract and SigNoz data flow
└── adapters/
    ├── claude-code/                   # Claude Code hook adapter
    ├── codex-cli/                     # Codex CLI notify adapter
    ├── codex-app-server/              # Desktop global hook installer and adapter
    └── omp/                           # OMP extension adapter
```

## From native surface to SigNoz

```mermaid
flowchart LR
    native[Native producer surface] --> adapter[Thin surface adapter]
    adapter --> runtime[session-context-runtime.sh]
    runtime --> git[Explicit workspace Git resolver]
    git --> record[Immutable canonical session-context record]
    record --> inbox[Local context inbox]
    otel[SigNoz source OTEL data\nproducer + correlation_id] --> scan[Bounded scanner]
    inbox --> scan
    scan --> activity[Canonical activity version]
    activity --> outbox[Immutable OTLP outbox]
    outbox --> signoz[SigNoz canonical activity event]
```

The scanner joins accepted context to source telemetry by the exact `(producer, correlation_id)` pair. `correlation_id` must equal the native session identifier proven for that surface; the runtime never derives it from prompts, telemetry content, CWD, process state, or local artifacts.

## Central record schema

```ts
type SessionContextEvent = {
  event_id: string // SHA-256 of producer, session ID, event type, time, and Git root
  producer: "claude-code" | "codex-cli" | "codex-app-server" | "omp"
  session_id: string
  event_type:
    | "session_start"
    | "workspace_changed"
    | "session_end"
    | "session_context" // Codex CLI only
  occurred_at: string // RFC3339 with an offset
  agent: {
    project: {
      id: string // SHA-256 of the normalized Git root
      name: string
      root: string // normalized absolute Git root
      kind: "git"
    }
  }
}
```

`session_context` records are non-temporal Codex CLI project evidence. The other event types produce project intervals. Every adapter must pass exactly `PRODUCER SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`; only the shared runtime resolves the Git root, derives IDs, and writes the schema.

## Codex app-server hook integration

Codex Desktop uses the canonical `codex-app-server` producer. Its installer, `adapters/codex-app-server/install.py`, installs the managed `adapter.py` beside the shared runtime and merges documented global hooks into `<codex-root>/hooks.json`, where `<codex-root>` is `$CODEX_HOME` when it is set to a non-empty absolute path and otherwise `~/.codex`; trust state is at `<codex-root>/config.toml`. The merge preserves unrelated hooks. Installation requires interactive trust; trust state is never written programmatically.

The installer-owned hooks are `SessionStart` with matcher `^(startup|resume|clear|compact)$` and `SessionEnd` with matcher `^other$`. The adapter accepts one hook JSON object on standard input and reads only documented `hook_event_name`, `session_id`, and absolute `cwd`, plus `source` for `SessionStart` or `reason` for `SessionEnd`. It never reads `transcript_path`, prompts, responses, or arbitrary payload fields.

`SessionStart` maps only to `session_start`; `SessionEnd` maps only to `session_end`. Because this envelope has no timestamp, the adapter captures synchronous UTC RFC3339 invocation time and execs the shared runtime with `codex-app-server SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`. The native session ID and cwd remain exact. Codex app-server does not support mid-thread project changes.
