# opencode Onboarding Pilot

Status: **measured 2026-10-13**. opencode stays a **candidate** producer.

This is the worked example for [Producer Onboarding](../references/producer-onboarding.md)
and the onboarding skill's
[producer onboarding workflow](../../.agents/skills/introspection-onboarding/references/producer-onboarding-workflow.md):
a producer the tool does not know, taken through identify, explore, assess, and
decide. The claims it produced are in
[`opencode.toml`](../../src/agent_introspection/harness_profiles/opencode.toml).

Every query was read-only and printed names and counts only. Unless stated, the window
is 2026-09-30 to 2026-10-11, the days opencode sent spans. Call and turn counts are of
distinct span IDs: on the two busiest days SigNoz stored some spans twice (about 16% of
the rows in turn traces, against 0.2% on 2026-10-11).

## Result

- Enabling opencode now, beside Claude Code, the three Codex surfaces, and omp, would
  hide 48 of the 53 signals visible today. Only Fact freshness, Tool calls, Tool failure
  rate, Failure signature, and Project concentration would stay.
- An opencode plugin adapter for the capabilities opencode's plugin surface offers would
  cut the cost to 8 hidden signals, and to 1 (Time to first token) once interrupts and
  steers are verified.
- Recommendation: keep opencode a candidate until a plugin adapter supplies at least
  model usage and the task boundary.

## Identify

`agent-introspection producers discover --days 7` classes `opencode` as
`known_producer` (named in the registry's `[producers]` table), with 17,352,432 span
rows, no logs, first seen 2026-10-09 07:37 UTC, and last seen 2026-10-11 11:41 UTC.
Over 30 days it sent spans on four days:

| Day        |      Spans | Traces |
| ---------- | ---------: | -----: |
| 2026-09-30 |    532,218 |  1,781 |
| 2026-10-09 | 11,389,121 | 15,458 |
| 2026-10-10 |  5,146,534 |  8,001 |
| 2026-10-11 |    816,777 |  1,528 |

## Explore

`producers profile opencode --days 1` found no rows, because the last span was two days
old. `--days 3` reaches the last day and part of the one before (5,963,311 span rows):

- 218 span names (the top 200 printed), no event names, no logs;
- 35 string and 1 number span attribute keys, and 9 resource keys (`opencode.run`,
  `opencode.client`, and `service.version` among them);
- `content_keys`: `db.query.text` (1,600,723 rows, all on `sql.execute`) and
  `url.query` (1,838, on the HTTP server spans).

The hint guesses were `native` for export, session identity, model usage, model
outcome, tool calls, and approvals, and `unverified` for the other nine. Three were
wrong leads: model usage and model outcome matched `LLM.run` by name, and approvals
matched `Permission.ask`, but none of those spans carries an attribute. The draft did
not validate until its binary, version arguments, verified versions, and session store
were filled in, as expected; the bundled profile has them.

The whole window was then assessed with time-bounded queries. A self-join over all
17.9M spans exceeded the local ClickHouse's memory limit (4 GiB here); bound such
queries to one day.

## Assess

| Capability         | Status                                                                            | Evidence in short                                                                          |
| ------------------ | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `export`           | native                                                                            | Spans only, under `service.name` opencode.                                                 |
| `session_identity` | native                                                                            | `session.id` on `Tool.execute` only; all 526 keys are store session IDs.                   |
| `task_boundary`    | hook_available (was unverified)                                                   | No span bounds a turn (below); plugin `session.status` and `chat.message` can.             |
| `model_usage`      | hook_available                                                                    | `tokens` on the assistant message of `message.updated`, one message per model step.        |
| `reasoning_usage`  | hook_available                                                                    | `tokens.reasoning` on the same message.                                                    |
| `model_identity`   | hook_available                                                                    | `modelID`, `providerID`, `variant`; whether `variant` is a reasoning effort is unverified. |
| `model_outcome`    | hook_available (was unverified), lacks Time to first token and Stream disconnects | `LLM.run` holds no outcome; `error` and times on the message; no time to first token.      |
| `tool_calls`       | native                                                                            | `Tool.execute`: 11,162 calls with name, call ID, session, and error flag; 418 failed.      |
| `tool_arguments`   | hook_available                                                                    | `tool.execute.before` hook.                                                                |
| `tool_failure`     | native (was hook_available)                                                       | A status message on 418 of 418 failed calls (381 distinct texts).                          |
| `user_friction`    | unverified                                                                        | No interrupt or steer observed (below).                                                    |
| `approvals`        | hook_available, lacks Sandbox outcomes                                            | `Permission.ask` spans are empty; `permission.ask` hook and `permission.replied` event.    |
| `delegation`       | hook_available (was unverified)                                                   | `session.created` carries `parentID` (below).                                              |
| `prompt_text`      | hook_available                                                                    | No logs; the `chat.message` hook sees the prompt.                                          |
| `session_store`    | native                                                                            | `[session_store_spec]` reads 3,271 sessions, and holds every telemetry key.                |

Hook and event payload fields come from the installed `@opencode-ai/plugin` and
`@opencode-ai/sdk` 1.2.6 type definitions. Their key names were also counted in the
events opencode keeps in the `event` table of `~/.local/share/opencode/opencode.db`.

### Task boundary

Of 26,768 traces, the roots are plumbing: `sql.execute` (13,932 traces),
`http.server GET` (4,134), four start-up spans (1,548 each), `http.server POST` (518),
and `Truncate.cleanup` (424). The turns are elsewhere:

- 1,546 traces each hold exactly one `SessionRunState.runner` and one
  `SessionPrompt.createUserMessage`, and between them 9,716 of the 9,787 `LLM.run` and
  11,079 of the 11,162 `Tool.execute` spans. One trace is one prompt.
- 1,538 of those 1,546 have no root span: the span that would hold the turn was never
  exported. The runner lasts about 0 s, so it marks only the start.
- `SessionPrompt.prompt`, from the synchronous prompt route, appears in only 31 turn
  traces. There it spans the turn's model calls.
- None of these spans carries an attribute. The session key comes only from a
  `Tool.execute` in the same trace: 1,423 turns have exactly one, none has two, and 123
  turns without a tool call have none.

A boundary could be pieced together from trace extents, but not with the session key
on every turn, so the claim is not `native`. The plugin `session.status` event
(`sessionID`; busy, idle) and the `chat.message` hook (`sessionID`, `messageID`) can
mark each prompt's start and end.

### Model outcome, user friction, and delegation

- **Model outcome.** None of the 9,787 `LLM.run` spans errored, and their median
  duration is 0.01 s, too short to be the call. In the store, each of the 32,347
  assistant messages with a `step-finish` part (of 32,367) has exactly one, so the
  message's `error`, `time.created`, and `time.completed` describe one model call; 5
  ended in `APIError`. No field holds time to first token or stream state, so the claim
  lacks Time to first token and Stream disconnects.
- **User friction.** No abort, cancel, interrupt, or steer span exists; none of the
  32,367 assistant messages ended in `MessageAbortedError`; the store's `session_input`
  queue table is empty. Neither an interrupt nor a prompt sent while busy has been
  observed, so the claim stays `unverified`.
- **Delegation.** No `task` tool call reached telemetry, but the store holds 9 such
  calls and 7 child sessions. The stored `session.created` events carry
  `info.parentID` on exactly those 7.

### Privacy

- `db.query.text` (4,716,761 `sql.execute` rows) holds statement templates, not user
  content: 88 distinct texts of at most 601 characters; 99.6% with `?` placeholders;
  538,328 parameterised `VALUES` and none with literal values; quoted literals in 1,884
  rows, all from one statement and none of 20 characters or more; no home path.
- The HTTP server spans do carry the user's paths: `url.query` holds a home-directory
  path in 5,018 of 5,028 rows (`url.full` repeats it), and the `x-opencode-directory`
  request header in 746 of 751.
- A failed `Tool.execute` span's status message is opencode's error text. SigNoz stores
  it as sent; once opencode is registered, the span route would keep one redacted line
  of at most 80 characters.

### Versions

The `opencode` on `PATH` prints 1.2.6, and the plugin and SDK packages are 1.2.6. The spans
came from a newer build that is not on `PATH`: their `service.version` is later than
1.2.6 and equals the session version of 3,250 of the 3,259 store sessions in the
window; 8 sessions came from 1.2.6. The profile keeps `verified_versions = ["1.2.6"]`
until that build is identified.

## Decide

Projection of the 58 harness-scoped signals:

| Outcome               | Draft from guesses | Before the pilot | After the pilot |
| --------------------- | -----------------: | ---------------: | --------------: |
| `reachable`           |                 23 |                3 |               5 |
| `reachable_with_work` |                  0 |               19 |              42 |
| `unknown`             |                 35 |               35 |               8 |
| `unreachable`         |                  0 |                1 |               3 |

`agent-introspection harness impact opencode` over `[harnesses] enabled` (`oh-my-pi`,
`codex-app-server`, `codex_cli_rs`, `codex_exec`, `claude-code`): 53 signals are visible
now. Cache creation, Sandbox outcomes, Stream disconnects, Output throughput, and Model
conformance are hidden already. The plugin columns come from scratch copies of the
profile with the plugin's capabilities marked present, run through
`harness impact <profile file>`:

| View                        | Visible now | With opencode now | With a plugin adapter | And friction verified |
| --------------------------- | ----------: | ----------------: | --------------------: | --------------------: |
| Pipeline                    |           3 |                 1 |                     3 |                     3 |
| Cache efficiency            |           7 |                 0 |                     7 |                     7 |
| Reasoning effort            |           9 |                 0 |                     9 |                     9 |
| Tool failures               |           6 |                 3 |                     6 |                     6 |
| Friction                    |           6 |                 0 |                     2 |                     6 |
| Verification and guardrails |           6 |                 0 |                     6 |                     6 |
| Provider                    |           6 |                 0 |                     5 |                     5 |
| Recurring failures          |           4 |                 1 |                     4 |                     4 |
| Requests and corrections    |           6 |                 0 |                     2 |                     6 |
| **Total**                   |      **53** |             **5** |                **44** |                **52** |

Today 39 of the 48 hidden signals are reachable with work, 8 unknown, and 1
unreachable. The capabilities in the way, by the number of hidden signals each blocks:
`task_boundary` 27, `model_usage` 9, `model_outcome` 8, `tool_arguments` 8,
`user_friction` 8, `prompt_text` 6, `reasoning_usage` 3, `model_identity` 3,
`approvals` 2, and `delegation` 1. An adapter that supplied only the task boundary and
model usage would leave 19 signals visible.

## Closing the gaps

1. **An opencode plugin adapter** for the activity hook route
   ([Hook Events](../references/hook-events.md)), like the omp extension in the
   onboarding skill's `scripts/adapters/omp/`: a plugin that hands each event to
   `scripts/activity-shim.sh`, an `opencode` producer in
   `src/agent_introspection/hooks.py`, and `hook_adapter = "opencode"` in the profile.
   Each field below must be checked on the build that runs, with counts only:

   | Plugin surface                                           | Capabilities                                                        | Fields to verify                                                                                                                    |
   | -------------------------------------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
   | `message.updated` (assistant message)                    | `model_usage`, `reasoning_usage`, `model_identity`, `model_outcome` | `tokens` (input, output, reasoning, cache read and write), `modelID`, `providerID`, `variant`, `error`, created and completed times |
   | `message.part.updated` (`step-finish` and `retry` parts) | `model_usage`, `model_outcome`                                      | per-step `tokens` and `reason`; retry `attempt`                                                                                     |
   | `session.status`                                         | `task_boundary`, `model_outcome`, `user_friction`                   | busy and idle per `sessionID`; retry attempt; what a prompt sent while busy produces                                                |
   | `chat.message` hook                                      | `task_boundary`, `prompt_text`, `user_friction`                     | `sessionID`, `messageID`, and the text parts, sent only as an opt-in prompt event                                                   |
   | `tool.execute.before` hook                               | `tool_arguments`                                                    | `args` per `callID`, reduced in memory, never stored                                                                                |
   | `permission.ask` hook, `permission.replied` event        | `approvals`                                                         | ask, allow, or deny; the user's response; whether configuration or the user decided                                                 |
   | `session.created` event                                  | `delegation`, `task_boundary`                                       | `info.parentID`, joined to the parent's `task` tool call                                                                            |
   | an interrupted turn                                      | `user_friction`                                                     | whether the assistant message ends in `MessageAbortedError`                                                                         |

2. **The session store.** The inline `[session_store_spec]` already reads the real
   database. On registration it moves to `session_stores.toml` as a `[[store]]`.
3. **The collector filter**
   ([`otel-collector-opencode-filter.yaml`](../../.agents/skills/introspection-onboarding/scripts/signoz/otel-collector-opencode-filter.yaml)).
   Live counts match its header: 29 span names, 16.57M of 17.88M rows (92.7%). It keeps
   every span the claims rely on (`Tool.execute`, `LLM.run`, `SessionRunState.runner`,
   `SessionPrompt.*`, `Permission.ask`). The only hint matches it
   removes are two `tool_calls` names, and 24 kept names still match that capability.
   No change was needed. It does not remove the paths on the HTTP server spans.
4. **The export switch.** The profile names no config file or setting, because how
   export is switched on has not been verified, so `harness configure` cannot manage it
   and `doctor` cannot check it.

## Recommendation

Under strict parity, opencode stays a candidate. Enabling it today would hide 48 of the
53 visible signals in exchange for opencode's tool calls and failures. Revisit once a
plugin adapter supplies
model usage and the task boundary. Even a complete adapter leaves Time to first token
hidden, and the eight friction and correction signals hidden until interrupts and
steers are verified; enabling then still needs the user to accept that cost.

## Next steps

1. Identify the opencode build that sends spans and how its export is switched on. Add
   its `[[config_file]]` and `[[setting]]` entries to the profile, and set
   `verified_versions` to that build.
2. Write the plugin adapter and the `opencode` normalizer, and verify each field in the
   table above on that build.
3. With the plugin listening, interrupt a turn and send a prompt while one runs; record
   how opencode reports each, then set `user_friction`.
4. Run a short session, then `agent-introspection producers profile opencode --days 1`
   and `agent-introspection harness impact opencode`. Move each claim the adapter
   delivers from `hook_available` to `hook`.
5. Decide with the user whether hiding Time to first token is acceptable.
6. If the user accepts the remaining cost, register opencode through the
   [change workflow](../../.agents/skills/introspection-operations/references/change-workflow.md),
   then save a baseline with `agent-introspection producers fingerprint --save opencode`.
7. Optional, and the user's decision: apply the collector filter, and decide whether to
   drop the path-bearing keys of the HTTP server spans as well.
