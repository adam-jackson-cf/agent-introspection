# Producer Onboarding

Use this reference to understand how Agent Introspection takes on a producer: one it
already knows (Claude Code, Codex, omp) or one it does not (opencode, or any other
agent harness). Both follow the same lifecycle and the same rules. The step workflows
are in the [`introspection-onboarding`](../../.agents/skills/introspection-onboarding/SKILL.md)
skill, the
[producer onboarding workflow](../../.agents/skills/introspection-onboarding/references/producer-onboarding-workflow.md)
among them; this page explains the model behind them.

## Purpose

Every dashboard signal consumes some kinds of producer data, called **capabilities**.
A **harness profile** records which capabilities one producer has. From the two, the
tool works out which signals the producer can reach, and what enabling it would hide
under the strict parity rule, before anyone changes a config file.

[Producer Capabilities](producer-capabilities.md) lists every capability: what to find
in a producer, how to check it, the hints that recognise it, and the signals that
require it. The [opencode pilot](../todo/opencode-onboarding.md) is the worked example
of an unknown producer taken through every step.

## Lifecycle

```text
identify ─▶ explore ─▶ assess ─▶ decide ─▶ configure ─▶ validate ─▶ watch
```

| Step      | Question                                                   | Command                                                                      |
| --------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------- |
| identify  | Which services send telemetry to SigNoz?                   | `producers discover`                                                         |
| explore   | What does one service send, by name?                       | `producers profile <service>`, with `--write PATH` to save the draft profile |
| assess    | Which capabilities does it have, and which signals follow? | the draft's capability guesses and `projection`, checked into a profile      |
| decide    | What would enabling it hide?                               | `harness impact <key or profile file>`                                       |
| configure | Switch on its export.                                      | `harness configure <key>`, `--dry-run` first                                 |
| validate  | Does every step work on this machine?                      | `doctor`, and `doctor --live` to send one empty OTLP request                 |
| watch     | Did an upgrade change what it sends?                       | `producers fingerprint --save`, then `doctor --drift`                        |

A known producer starts at **decide**: its profile is bundled and already checked, and
`harness list` shows it. An unknown producer starts at **identify**, and goes on from
**decide** to **configure** only once it has been added as a harness, a code change
(see below); until then it stays a candidate. Every command is in the
[CLI Reference](cli-reference.md#onboarding).

## Known and candidate producers

- A **registered** profile covers one or more registry `[harnesses]` keys (`codex`
  covers the Codex app-server, CLI, and exec surfaces). Its claims must agree with the
  registry: a test asserts that every registry cell it reaches (`aligned` or
  `differs`) has each required capability `native` or `hook`. `harness configure`
  writes its settings, and `doctor` checks it when it is enabled.
- A **candidate** profile describes a producer that is not a harness yet, from
  measured evidence. It covers no registry key, may name no config files or settings
  (until its export switch is verified, `harness configure` has nothing to write), and
  may carry its session store inline. `harness configure` refuses a candidate;
  `harness impact` previews it. opencode is the bundled candidate.
- `producers discover` classes every service: `registered` (a registry harness),
  `known_producer` (listed in the registry's `[producers]` table, as opencode is),
  `internal` (this tool's own records), or `unregistered` (anything else).

Turning a candidate into a harness is a code change: a registry `[harnesses]` key, a
route or a reason for every signal, a store in `session_stores.toml`, the profile with
status `registered`, and an activity-hook adapter where one is needed. The
`introspection-operations` skill's
[change workflow](../../.agents/skills/introspection-operations/references/change-workflow.md)
lists every place a harness is named.

## Capabilities and signals

The registry
([`signal_support.toml`](../../src/agent_introspection/facts_sql/signal_support.toml))
declares the 15 capabilities under `[[capability]]`, and every harness-scoped signal
lists the ones it `requires`: Project attribution, for example, requires
`session_identity`, `task_boundary`, and `session_store`. System signals, which
describe the pipeline or the improvement loop rather than a harness, require none.

Each capability has hints: case-insensitive regular expressions matched against span
names, event names, and attribute keys. `producers profile` uses them to guess which
capabilities an unknown producer has. A match is a lead, not evidence: a key such as
`thread.id` can match `session_identity` without being a session key. Each claim is
confirmed by the capability's **check** before it goes into a profile.

## Harness profiles

Profiles live in the `src/agent_introspection/harness_profiles/` package, one
`<key>.toml` per producer (schema `agent-introspection.harness-profile/1`), read and
validated by `src/agent_introspection/profiles.py`. A draft from
`producers profile --write` uses the same schema and can be anywhere; `harness impact`
loads it by path. Drafts are never written into the bundled package.

| Key                                           | What it holds                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `key`, `title`                                | The profile key (the file name) and the display name.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `status`                                      | `registered` or `candidate`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `registry_keys`                               | The `[harnesses]` keys it covers; empty for a candidate.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `service_names`                               | The `service.name` values it exports under; the first fills `{service_name}`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `binary`, `version_args`, `verified_versions` | The command, the arguments that print its version, and the versions its claims were checked on. `harness list` and `doctor` compare the installed version with these.                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `session_store` or `[session_store_spec]`     | A `[[store]]` name in `session_stores.toml`, or, for a candidate only, an inline store of the same shape (see [Project Attribution](project-attribution.md)).                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `hook_adapter`                                | The activity-hook producer key (`claude-code` or `omp`), or empty.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `sources`                                     | Documentation URLs behind the claims.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `[[config_file]]`                             | `id`; `paths` (a leading `~`, `$VAR`, and `${VAR}` expand; a path whose variable is unset is skipped); `format` (`json`, `toml`, or `dotenv`); `table`, the dotted path of the settings table (`""` for the top level); and `roots`: `first` (the first path whose variables are set, existing or not: a set config-directory variable such as `CLAUDE_CONFIG_DIR` replaces the default location, as it does for the harness) or `each` (every path that names a set variable, and every other whose directory exists, as for Codex homes). `harness configure` writes these files and `doctor` reads the same ones. |
| `[[setting]]`                                 | `file`, `key`, `value`, `group` (`export`, or the opt-in `prompt_export`), and `purpose`. A value is a literal (text, boolean, integer, or table) or a template with `{otlp_endpoint}`, `{otlp_protocol}`, or `{service_name}`.                                                                                                                                                                                                                                                                                                                                                                                      |
| `[capabilities]`                              | One claim per capability: `status`, and optionally `route` (a registry route of this profile), `evidence`, and `lacks`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `[capability_overrides.<registry key>]`       | Claims that replace the profile's for one surface, such as `user_friction = not_applicable` for headless Codex exec.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

`{otlp_endpoint}` is config `[signoz] otlp_endpoint` (default `http://127.0.0.1:4318`), or
on an additional machine `[machine] export_endpoint`, the host's collector. Both are
OTLP/HTTP base URLs, and config refuses the OTLP/gRPC port 4317 there, so every profile
writes one transport: `{otlp_protocol}` is always `http/protobuf`, and
`{service_name}` is the first service name.

A capability claim has one status:

- `native`: in the producer's own telemetry now;
- `hook`: an installed activity hook supplies it;
- `hook_available`: a documented hook or plugin surface exists, but no adapter yet;
- `missing`: the producer does not offer it;
- `not_applicable`: it cannot exist for this producer or surface;
- `unverified`: not yet checked.

A claim takes its weakest part. omp's `model_outcome` is `hook`, for example, because
its retries come from an activity hook although the rest is native. Where a capability
is coarser than a signal, `lacks` names the signals it cannot serve, and for those the
claim counts as `missing`. Codex `model_usage`, for example, has no cache writes, so it
lacks Cache creation.

Loading checks every profile against the registry and the session stores: a route must
be one of the profile's registry routes, and a route from an activity hook needs status
`hook`; `hook` needs a `hook_adapter`; `lacks` may name only signals that require the
capability; a registered profile names at least one export setting and a store from
`session_stores.toml`; every registry harness has exactly one registered profile; and
no name resolves to two profiles.

## Projection and impact

Projection maps a profile's claims onto every harness-scoped signal. Each signal gets
one outcome, from the statuses of the capabilities it requires (the first that applies
wins):

| Outcome               | When                                                       |
| --------------------- | ---------------------------------------------------------- |
| `not_applicable`      | A required capability is `not_applicable`.                 |
| `unreachable`         | One is `missing`, or its claim `lacks` the signal.         |
| `unknown`             | One is `unverified`, or not claimed at all.                |
| `reachable_with_work` | The rest are present and at least one is `hook_available`. |
| `reachable`           | Every required capability is `native` or `hook`.           |

`harness impact` applies the strict parity rule over config `[harnesses] enabled`. It
lists the signals visible now, the ones still visible if the producer were enabled
too, and each signal it would hide, with the most severe outcome, the capabilities in
the way (`missing`), and the registry's note. Parity is strict: `unknown` and
`reachable_with_work` hide a signal as surely as `unreachable`, because the dashboard
shows only what every enabled harness reaches today. For the surfaces of a registered
profile, the registry's cell decides, not the projection. A producer is enabled only
when the user accepts every signal it would hide.

## Transport equivalence

A profile's export settings template the collector's endpoint and protocol, normally
OTLP/HTTP on port 4318. A setting whose current value already reaches the local
collector another way is equivalent: OTLP/gRPC on port 4317 (a Codex `otlp-grpc`
exporter, or protocol `grpc`), or OTLP/HTTP on port 4318, or the configured endpoint's
port, at the same path. `doctor` passes such a setting, and `harness configure`
reports it `satisfied` and leaves it alone; `harness configure --force` rewrites it to
the profile's value. Both commands share the rule (`profiles.equivalent_transport`).

## Fingerprints and drift

A fingerprint records one service's span names, event names, and attribute keys
(`<source>.<kind>:<key>`), with row counts, over a window.
`producers fingerprint --save` writes one per service to
`~/.local/share/agent-introspection/fingerprints/<service>.json`; save one after
onboarding a producer and after reviewing an upgrade.

`doctor --drift` compares each enabled harness's names now with its saved fingerprint,
over the saved fingerprint's window:

- a name is **added** when it has rows now and was not saved, and **removed** only when
  it was saved and has no rows now; changed counts are not drift;
- a list that hit the 5,000-name limit is reported under `incomplete` and not judged in
  that direction;
- each service's status is `unchanged`, `drifted`, `no_baseline`, or
  `invalid_baseline`;
- services with rows in the last 7 days that no registry table knows (class
  `unregistered`) are reported separately; they do not count as drift;
- rows in that window with an empty `service.name` are counted (`unnamed_rows`, which
  `doctor --drift` reports as `drift_unnamed`): their sender needs
  `OTEL_SERVICE_NAME`.

Drift is a prompt to explore: `producers profile <service>` shows what changed, and a
capability that depends on a changed name is checked again before a new baseline is
saved. A version that passes this review can then be added to the profile's
`verified_versions`.

## Privacy

- Exploration reads names and counts only: span names, event names, and resource and
  attribute keys. The only value read is the log `event.name`, which the projections
  already read as the event name. Home directories in names are shown as `~`.
- `producers profile` flags string attribute keys whose last word suggests user text
  (prompts, commands, tool arguments or output, message bodies, query text) under
  `content_keys`. Their values are never read.
- Secrets are checked for presence only: `doctor` never runs a key command and never
  prints a hooks endpoint taken from the environment. `harness configure` and `doctor`
  print only the keys a profile manages, with unmanaged parts hidden.
- An assessment by hand follows the same rules: read-only queries with
  `SETTINGS max_execution_time`, printing names and counts, never attribute values,
  prompts, commands, SQL text, or secrets.
- A producer that floods SigNoz with plumbing can be filtered at the collector, as the
  opt-in opencode filter in the onboarding skill's
  [`scripts/signoz/`](../../.agents/skills/introspection-onboarding/scripts/signoz/README.md)
  does. Applying it changes the user's SigNoz, so it is their decision.

## Related Docs

- [Producer Capabilities](producer-capabilities.md): every capability, its hints, and
  the status each profile claims.
- [CLI Reference](cli-reference.md#onboarding): the onboarding commands and their
  output.
- [Data Gaps](data-gaps.md#harness-support-matrix): what each enabled harness keeps
  hidden today.
- [Project Attribution](project-attribution.md): session stores, including SQLite
  stores and a candidate's inline store.
