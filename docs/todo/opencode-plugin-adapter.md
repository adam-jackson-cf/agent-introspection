# opencode plugin adapter

Track: priority: CLI data access / problem and friction surfacing

## Problem

opencode is a candidate producer. Enabling it today would hide most dashboard signals under the parity
rule, because its own spans carry only tool calls and failures. A pilot measured on 2026-10-13 found that
opencode's plugin surface offers most missing capabilities. No adapter reads it yet.

## Evidence

- `src/agent_introspection/harness_profiles/opencode.toml:4,9,10`: `status = "candidate"`,
  `verified_versions = ["1.2.6"]`, `hook_adapter = ""`. The profile has no `[[config_file]]` or
  `[[setting]]`, so `harness configure` and `doctor` cannot manage its export.
- `src/agent_introspection/hooks.py:23`: `PRODUCERS = ("claude-code", "omp")`.
- The profile's per-capability `evidence` strings hold the pilot's findings. Capabilities still
  `hook_available`: `task_boundary`, `model_usage`, `reasoning_usage`, `model_identity`,
  `model_outcome`, `tool_arguments`, `approvals`, `delegation`, `prompt_text`. `user_friction` is
  `unverified`.
- The spans came from a newer opencode build than the 1.2.6 on `PATH`.

## Proposed change

1. Identify the opencode build that sends spans and how its export is switched on. Add its
   `[[config_file]]` and `[[setting]]` entries and set `verified_versions`.
2. Write a plugin that hands each event to `hook_runtime/activity-shim.sh`, like the omp extension in
   `hook_runtime/omp/`. Add an `opencode` producer to `hooks.py` and set `hook_adapter = "opencode"`.
   Verify each field on the running build, with counts only:

   | Plugin surface                                    | Capabilities                                                        | Fields to verify                                                                        |
   | ------------------------------------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
   | `message.updated` (assistant message)             | `model_usage`, `reasoning_usage`, `model_identity`, `model_outcome` | `tokens` (with reasoning and cache), `modelID`, `providerID`, `variant`, `error`, times |
   | `message.part.updated` (`step-finish`, `retry`)   | `model_usage`, `model_outcome`                                      | per-step `tokens` and `reason`; retry `attempt`                                         |
   | `session.status`                                  | `task_boundary`, `model_outcome`, `user_friction`                   | busy and idle per `sessionID`; what a prompt sent while busy produces                   |
   | `chat.message` hook                               | `task_boundary`, `prompt_text`, `user_friction`                     | `sessionID`, `messageID`; text only as an opt-in prompt event                           |
   | `tool.execute.before` hook                        | `tool_arguments`                                                    | `args` per `callID`, reduced in memory, never stored                                    |
   | `permission.ask` hook, `permission.replied` event | `approvals`                                                         | ask, allow or deny; whether configuration or the user decided                           |
   | `session.created` event                           | `delegation`, `task_boundary`                                       | `info.parentID`, joined to the parent's `task` tool call                                |
   | an interrupted turn                               | `user_friction`                                                     | whether the assistant message ends in `MessageAbortedError`                             |

3. Interrupt a turn and send a prompt while one runs, then set `user_friction`.
4. Move each delivered claim from `hook_available` to `hook`, and rerun
   `agent-introspection harness impact opencode`.
5. Decide with the user whether hiding Time to first token (no field holds it) is acceptable. If so,
   register opencode through the signals change workflow and save a fingerprint baseline.
6. Optional, the user's decision: apply the opencode collector filter, and drop the path-bearing keys
   (`url.query`, `url.full`, `x-opencode-directory`) of the HTTP server spans.

## Acceptance check

`agent-introspection harness impact opencode` shows at most 9 hidden signals with the adapter, and at
most 1 (Time to first token) once friction is verified.
