# Producer onboarding workflow

Take a producer that is not a registered harness (opencode, or any other agent
harness) from first sight to a decision: add it as a harness, or leave it a candidate.
The same steps watch the registered harnesses for changes after an upgrade.
[Producer Onboarding](../../../../docs/references/producer-onboarding.md) explains the
model behind these steps, and the
[opencode pilot](../../../../docs/todo/opencode-onboarding.md) is the worked example.

```text
identify ─▶ explore ─▶ assess ─▶ decide ─▶ add (or keep a candidate) ─▶ configure ─▶ validate ─▶ watch
discover    profile    capability harness   change workflow             harness       doctor      fingerprint
                       reference  impact                                configure     (--live)    doctor --drift
```

Each dashboard signal consumes some kinds of producer data, called capabilities.
[Producer capabilities](../../../../docs/references/producer-capabilities.md) says,
for each capability, what to find in a producer, how to check it, and which signals
need it. A harness profile (`src/agent_introspection/harness_profiles/<key>.toml`)
records one producer's status for each capability, with the route and evidence.

The `producers` commands read names and counts, never values. Keep any query of your
own to the same: never print attribute values, prompts, commands, SQL text, or
secrets.

## Identify

`agent-introspection producers discover [--days N]` (default 7 days) lists every
`service.name` with spans or logs in SigNoz, with row counts and first and last seen
(UTC), in one of four classes:

- `registered`: a registry harness, with `enabled` and its `profile` key;
- `known_producer`: named in the registry's `[producers]` table (opencode), not a
  harness;
- `internal`: Agent Introspection's own records;
- `unregistered`: anything else: a new producer, a new surface of a known one, or a
  test sender.

Rows with an empty `service.name` are one `unregistered` entry named `(empty)`, with a
`note`: no command can select them until their sender sets `OTEL_SERVICE_NAME`.

`enabled_not_seen` lists enabled harnesses that sent nothing in the window. Confirm
with the user which service is the producer. If it is absent, find its OTLP settings
in its own docs, point them at the local collector (`[signoz] otlp_endpoint`) with a
stable `service.name` (back up and diff each file first), run a short session, and
discover again.

## Explore

`agent-introspection producers profile <service> [--days N]` lists the service's span
names, event names, and resource and attribute keys, each with a row count, never a
value. Each list stops at 200 names, with `distinct` and `dropped` counts and one
stderr line per cut list; home directories in names print as `~`. Each query is capped
at 120 seconds, so for a busy producer use `--days 1` (opencode sent millions of spans
a day). The output also holds:

- `content_keys`: string attribute keys whose last word suggests user text (prompts,
  commands, tool arguments or output, message bodies, query text). Their values are
  never read. Record them: no projection may store their values.
- `capabilities`: a `note` and `guesses`, a list with one entry per capability: `id`,
  `title`, `guess` (`native` or `unverified`) from the hint patterns in the registry,
  and `evidence`, the names that matched with their row counts (`more_evidence` counts
  the matches not listed). A guess is a lead, not evidence: hints can match the wrong
  thing (a `thread.id` key matched session identity, and a `sandboxed` key matched
  approvals, on a Codex surface).
- `draft`, a candidate profile built from the guesses, with `draft_check`;
  `projection`, its outcome per signal; and `impact` over the enabled harnesses.

`--write PATH` writes the draft as TOML to a working file. Before any query, it
refuses a path inside the bundled `harness_profiles/` directory, an existing file
without `--force`, a directory, or a missing parent directory.

A producer that sends large volumes of internal spans or text can be filtered at the
collector. `scripts/signoz/otel-collector-opencode-filter.yaml` drops opencode's
plumbing spans and its SQL text; applying it changes SigNoz, so it is the user's
decision, and its header says how to apply and revert it.

## Assess

Work through the draft against the capability reference, one capability at a time:

1. Verify each guess with the capability's check. Use counts, never printed values:
   for example, how many session keys on the producer's usage rows also appear on its
   tool rows.
2. Set each capability's `status` (`native`, `hook`, `hook_available`, `missing`,
   `not_applicable`, or `unverified`) with its `evidence`, and its `route` once a
   registry route reads it. `lacks` names signals a capability is too coarse for.
3. From the producer's installed docs, types, and config, fill what telemetry cannot
   show: `binary`, `version_args`, `verified_versions`, and a session store, either
   `session_store` (a `[[store]]` name in `session_stores.toml`) or an inline
   `[session_store_spec]`. A store is JSON Lines (one file per session) or SQLite (one
   database: `format = "sqlite"` and a `query` returning `session_id`, `cwd`, and
   `started_at`); `session_stores.toml` documents both. `draft_check.to_complete` lists
   what is still empty; `harness impact` loads a draft file only once these are set.
4. When its export settings are known, add `[[config_file]]` and `[[setting]]` entries
   (groups `export` and `prompt_export`), so `harness configure` can write them once it
   is registered.
5. For each gap telemetry cannot fill, look for a documented hook or plugin event and
   its payload fields; a gap such an event could fill is `hook_available`.

## Decide

`agent-introspection harness impact <profile key, service, or draft file>` compares the
enabled harnesses (`[harnesses] enabled`) with and without the producer: the signals
visible now, visible after, and `hidden`, each hidden signal with its outcome, the
missing capabilities, and a note. Parity is strict: enabling a producer hides every
signal it cannot reach today, including those reachable only with work
(`hook_available`) or not yet verified (`unverified`).

Show the user the hidden list. The user decides:

- **close gaps first**, then add it (next section);
- **add it now**, accepting that the hidden signals disappear on every machine that
  enables it; or
- **leave it a candidate**: nothing changes. To keep the assessment, add the profile
  to `harness_profiles/` in a change, with `status = "candidate"` and empty
  `registry_keys` as opencode's has, so `harness list` and `harness impact` know it.

## Close the gaps and add it

- **Hooks:** a hook or plugin adapter under `scripts/adapters/<producer>/`, with its
  events in `hooks.py` and `docs/references/hook-events.md`, turns `hook_available`
  into `hook`.
- **Session store:** a `[[store]]` entry in `session_stores.toml`; SQLite stores are
  supported.
- **Registration:** follow the `introspection-operations` change workflow, "Add a
  harness". The profile becomes `registered`, and the registry and dashboard changes
  ship in one change.

## Configure and validate

Run the [harness configuration workflow](harness-configuration-workflow.md) for the new
harness (it is now in `[harnesses] enabled`), then the
[validation workflow](validation-workflow.md).

## Watch

Producers change with upgrades: names are renamed, added, or dropped.

- **Baseline.** After onboarding a harness, and after verifying an upgrade, save its
  fingerprint: `agent-introspection producers fingerprint --save [service …]`
  (default: the enabled harnesses). It records span names, event names, and attribute
  keys with counts, up to 5,000 per list, in
  `~/.local/share/agent-introspection/fingerprints/<service>.json`. Without `--save`
  it only prints.
- **Drift.** `agent-introspection doctor --drift` compares each enabled harness's
  telemetry with its saved fingerprint over the same window. A service is `unchanged`
  (pass), `drifted` (warn, counting the names added and removed), `invalid_baseline`
  (warn), or `no_baseline` (info). A name counts as removed only at 0 rows; changed
  counts are not drift; lists cut at the limit are reported as `incomplete` and not
  judged. `doctor`'s `binary` check also flags a version newer than the profile's
  `verified_versions`.
- **New services.** A service the registry does not know, such as a new Codex surface
  with its own `service.name`, shows as `unregistered` in `producers discover` and as
  a warning in `doctor --drift`. Explore it as above, then decide whether it joins an existing
  profile and the registry as another surface (a change) or is a separate producer.
- **Renames.** A route going quiet while a similar name appears is a rename: fix the
  route with the `introspection-operations` change workflow, bump `verified_versions`
  in the profile, and save the fingerprint again.

## Done when

- The producer is registered, configured, and validated, or it stays a candidate with
  its profile and the user's decision recorded; and each enabled harness has a saved
  fingerprint.
