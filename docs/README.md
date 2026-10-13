# Documentation Index

Start at the root [README](../README.md) for install and first run.

## references/

- [cli-reference](references/cli-reference.md): every `agent-introspection` command, output contract, and exit code (operators).
- [dashboard-companion](references/dashboard-companion.md): server binding, environment variables, how views aggregate harnesses (dashboard developers).
- [data-gaps](references/data-gaps.md): what the dashboard cannot show, per harness and signal; contains the generated harness support matrix (anyone reading a dashboard).
- [development](references/development.md): quality gates, pre-commit, dashboard package commands (contributors).
- [facts-store](references/facts-store.md): how telemetry becomes fact views, snapshots, registry tables, and the standing decisions (contributors).
- [hook-events](references/hook-events.md): activity-hook record contract, route, delivery, prompt export (contributors and operators).
- [producer-capabilities](references/producer-capabilities.md): what each producer capability needs, how to check it, the signals that require it, and the status each profile claims; generated from the registry and the profiles (anyone onboarding a producer).
- [producer-onboarding](references/producer-onboarding.md): the onboarding lifecycle, known and candidate producers, harness profiles, projection and parity impact, fingerprints and drift, privacy rules (operators and contributors).
- [project-attribution](references/project-attribution.md): how sessions are attributed to Git projects, including SQLite session stores (contributors and operators).
- [proposal-lifecycle](references/proposal-lifecycle.md): findings to evaluated proposals (operators and improvement agents).
- [signal-catalog](references/signal-catalog.md): producer routes and calculations behind every dashboard signal (view builders).
- [signoz-connection](references/signoz-connection.md): finding SigNoz, the opt-in guided install, the two SigNoz layouts, connection modes, and ClickHouse grants (operators).

## Other folders

- [ux/](ux/AGENTS.md): design and UX standards; `ux/AGENTS.md` is the index.
- [todo/](todo/decision-models-plan.md): proposed, unscheduled plans.
- [guardrails/](guardrails/): machine JSON read by `validate-rule-coverage.py`, the repo's lint-rule ledger; not the dashboard's Guardrails page.
