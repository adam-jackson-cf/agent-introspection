# Dashboard UX improvements

Append-only record of agreed dashboard changes. No dashboard changes are made
until the user says: "please make these improvements". At that point, prepare a
plan for approval before implementation.

## 2026-07-20 — Dashboard purpose split

- Create a separate **Agent Introspection Health** dashboard for operational
  process health.
- Keep **Agent Introspection** focused on observed behaviours, evidence, and
  prioritisation.
- Move pipeline health and scan-run performance views to **Agent Introspection
  Health**.

## 2026-07-20 — Pipeline health

- Reduce the Pipeline health table to: **Pipeline state**, **Last completed
  scan**, and **Last scan duration**.
- Do not surface terminal status, freshness, logs, or traces elsewhere in either
  dashboard. Continue capturing them in telemetry for diagnosis.

## 2026-07-20 — Recent scan runs

- Replace the standalone Scan duration chart with a **Recent scan runs** table
  on **Agent Introspection Health**.
- Show the latest 24 completed scans, ordered newest first.
- Include: **Started at**, **Duration**, **Outcome**, and **Rows processed**.

## 2026-07-20 — Project data attribution

- Rename Project identity coverage to **Project data attribution**.
- Use plain-language column labels: **Project attribution coverage**,
  **Attributed observations**, and **All observations**.
- Treat trustworthy project attribution as a prerequisite for assigning a
  detected pattern to a project and creating a project-specific recommendation.
- Establish a trustworthy mapping from the workspace/thread context used by
  Codex, then reanalyse the canonical seven-day window.
- Keep any remaining unresolved share visible. Unassigned findings can describe
  cross-project patterns but must not drive a project-specific recommendation or
  proposal.

## 2026-07-20 — Actionable trends

- Keep the current table structure and shorten its title to **Actionable
  trends**.
- Make the evidence time window explicit so an occurrence count cannot be read
  as activity from the evaluation timestamp alone. Detailed column changes are
  pending agreement.
- Add a visual view of actionable issue composition and concentration: the
  proportion of actionable issue occurrences by category and, once attribution
  is trustworthy, by project. The visual design is pending agreement.
- Replace **Current trend context** with a clear finding-status distribution
  visual, or absorb it into the actionable issue-composition view after the
  SigNoz capability review. Use human-readable state labels rather than raw
  key-value legend text.

## 2026-07-20 — Analysis timeframe

- Make the selected analysis timeframe explicit in the Agent Introspection
  dashboard grouping and panel labels.
- Provide selectable review horizons of **7 days**, **14 days**, **30 days**,
  and **60 days**. Preserve the canonical seven-day view as the default until
  each horizon has a valid, separately bounded analysis projection and proven
  source retention.
- Use the existing SigNoz **Last 1 week** timeframe selector as the current
  user control. Make its relationship to the data shown in every grouped panel
  clear through the dashboard UX.
- Do not add custom horizon controls as part of the currently agreed dashboard
  improvements. Reassess wider horizons only after the SigNoz capability review
  and a validated data-contract design.

## 2026-07-20 — Panel purpose labels

- Give every retained insight panel a concise user-facing subtitle that states
  the question it answers. Confirm the supported SigNoz implementation pattern
  during the final capability review.
- Use this subtitle for **Detector signal yield**: **Share of distinct findings
  that become actionable patterns.**
- Write every panel subtitle in concise, user-facing language.

## Final discovery step before dashboard implementation

- Complete a deep-dive review of supported SigNoz dashboard capabilities,
  including chart types, dashboard variables, time controls, panel labels, and
  interaction patterns. Use the findings to validate the approved dashboard UX
  plan before implementation.

## 2026-07-20 — Review activity removal

- Remove the **Review activity** dashboard panel and its supporting aggregate
  persistence and telemetry.
- Retain the `introspection-review` workflow and the validated candidate,
  classification, and proposal records required to action findings.
