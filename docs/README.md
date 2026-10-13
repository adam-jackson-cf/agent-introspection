# Documentation Index

Start at the root [README](../README.md) for install and first run.

## references/

- [cli-reference](references/cli-reference.md): every `agent-introspection` command and output contract (operators).
- [dashboard-companion](references/dashboard-companion.md): server binding, environment variables, how views aggregate harnesses (dashboard developers).
- [data-gaps](references/data-gaps.md): what the dashboard cannot show, per harness and signal; contains the generated harness support matrix (anyone reading a dashboard).
- [development](references/development.md): quality gates, pre-commit, dashboard package commands (contributors).
- [facts-store](references/facts-store.md): how telemetry becomes fact views, snapshots, registry tables, and the standing decisions (contributors).
- [hook-events](references/hook-events.md): activity-hook record contract, route, delivery, prompt export (contributors and operators).
- [project-attribution](references/project-attribution.md): how sessions are attributed to Git projects (contributors and operators).
- [proposal-lifecycle](references/proposal-lifecycle.md): findings to evaluated proposals (operators and improvement agents).
- [signal-catalog](references/signal-catalog.md): producer routes and calculations behind every dashboard signal (view builders).
- [signoz-connection](references/signoz-connection.md): connection modes and ClickHouse grants (operators).

## Other folders

- [ux/](ux/AGENTS.md): design and UX standards; `ux/AGENTS.md` is the index.
- [todo/](todo/decision-models-plan.md): proposed, unscheduled plans.
- [guardrails/](guardrails/): machine JSON read by `validate-rule-coverage.py`, the repo's lint-rule ledger; not the dashboard's Guardrails page.
