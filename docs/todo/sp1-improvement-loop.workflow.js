export const meta = {
  name: 'sp1-improvement-loop',
  description: 'SP1: draft proposals for the top 3 findings with the real CLI, blind-diagnose them independently, judge each draft',
  phases: [
    { title: 'Draft', detail: 'sequential real `proposal draft` runs on the primary host' },
    { title: 'Diagnose', detail: 'independent blind root-cause per finding' },
    { title: 'Judge', detail: 'compare draft vs diagnosis against the facts' },
  ],
}

const HOST = 'adams-macbook-pro.tailf3b31b.ts.net'
const ACCESS = `
Access (all read-only unless stated):
- SSH: \`ssh -o BatchMode=yes ${HOST} '<cmd>'\`.
- Facts store (ClickHouse, sanitized, no prompt/command text): on the host run \`/tmp/ch-ro.sh "<SQL>"\` (readonly=1). Key tables in database \`introspection\`: tool_calls_snapshot (ts, harness, session_id, call_id, tool, outcome, exit_code, duration_ms, command_head, command_sub, targets, task_id, workdir, tool_family, failure_signature, failure_class), task_outcomes_snapshot, and other views (\`SHOW TABLES FROM introspection\`).
- Agent Introspection source: /Users/adamjackson/Projects/inflight/agent-introspection on this Mac (findings.py defines the detector; facts_sql/003_views.sql defines failure_class).
- The user's projects live on THIS Mac under ~/Projects (the primary host mostly has none). omp's own config/extensions: ~/.omp on either machine. Codex config: ~/.codex.
Never print secrets or env values; never modify files, configs, or stores.`

const FINDING_DESC = f => `finding ${f.id}: tool_family=${f.tool_family}, failure_class="${f.failure_class}", harnesses=${JSON.stringify(f.harnesses)}, impact=${f.impact} (affected tasks over the last 7 Europe/London days, unclean tasks counted twice)`

const DRAFT_SCHEMA = {
  type: 'object',
  properties: {
    finding_id: { type: 'string' },
    drafted_finding_id: { type: 'string' },
    ran: { type: 'boolean' },
    exit_code: { type: 'integer' },
    proposal_id: { type: 'string' },
    duration_seconds: { type: 'number' },
    tokens_charged: { type: 'integer' },
    codex_workdir: { type: 'string' },
    root_cause: { type: 'string' },
    intervention: { type: 'string' },
    chosen_tier: { type: 'string' },
    tier_audit: { type: 'string' },
    success_metric: { type: 'string' },
    proposal_json: { type: 'string', description: 'the stored proposal payload, verbatim JSON' },
    errors: { type: 'string' },
  },
  required: ['finding_id', 'ran', 'proposal_json', 'errors'],
}

const DIAG_SCHEMA = {
  type: 'object',
  properties: {
    finding_id: { type: 'string' },
    classification: { type: 'string', enum: ['environment_or_sandbox_config', 'agent_behaviour', 'benign_exploration', 'tool_or_harness_defect', 'detector_artifact', 'mixed', 'undetermined'] },
    root_cause: { type: 'string' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    evidence: { type: 'array', items: { type: 'string' } },
    real_cost: { type: 'string', description: 'what this costs: wasted calls, time, derailed tasks — with numbers from the facts' },
    recommended_intervention: { type: 'string' },
    intervention_tier: { type: 'string', enum: ['established_tool', 'new_tool', 'instruction', 'detector_change', 'none_needed'] },
    worth_fixing: { type: 'boolean' },
  },
  required: ['finding_id', 'classification', 'root_cause', 'confidence', 'evidence', 'real_cost', 'recommended_intervention', 'intervention_tier', 'worth_fixing'],
}

const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    finding_id: { type: 'string' },
    root_cause_agreement: { type: 'string', enum: ['match', 'partial', 'mismatch', 'no_draft'] },
    draft_claims_checked: { type: 'array', items: { type: 'object', properties: { claim: { type: 'string' }, supported: { type: 'boolean' }, check: { type: 'string' } }, required: ['claim', 'supported', 'check'] } },
    intervention_fit: { type: 'string', enum: ['good', 'acceptable', 'poor', 'no_draft'] },
    advisory_decision: { type: 'string', enum: ['approve_as_is', 'approve_with_minor_edits', 'reject', 'no_draft'] },
    suggested_edits: { type: 'string' },
    reasons: { type: 'string' },
    loop_defects: { type: 'array', items: { type: 'string' }, description: 'defects in the improvement loop itself exposed by this draft (detector, evidence pack, drafting environment, metric)' },
  },
  required: ['finding_id', 'root_cause_agreement', 'draft_claims_checked', 'intervention_fit', 'advisory_decision', 'reasons', 'loop_defects'],
}

const draftPrompt = (f, n) => `You are executing one step of an approved experiment (spike SP1): run Agent Introspection's real proposal drafter once, on the primary host, and report what it produced. This run is ${n} of 3; runs are strictly sequential because each draft takes the highest-impact actionable finding without a proposal.

Expected target: ${FINDING_DESC(f)}.

This is a rerun: launch from /tmp/sp1-scratch (an empty git repo on the host; Codex exec only passes its trust check inside a git repo, verified) — earlier attempts from /tmp and ~/Projects failed with exit 60. Leftover /tmp/sp1-* files from this run can be overwritten.

Steps:
1. Dry run to confirm the target: \`ssh -o BatchMode=yes ${HOST} 'cd /tmp/sp1-scratch && /tmp/ai-primary.sh proposal draft --reserved-model-budget 2000000 --dry-run > /tmp/sp1-dry-${n}.json 2>&1; echo rc=$?'\` then read review.ordered_candidate_ids[0] from that JSON (python3 on the host). If it is NOT ${f.id}, STOP: do not draft; return ran=false with the id you saw in drafted_finding_id and explain in errors.
2. Real run (this spends model tokens and writes a pending proposal — authorized). Codex can take >10 minutes, so detach and poll:
   \`ssh -o BatchMode=yes ${HOST} 'cd /tmp/sp1-scratch && nohup sh -c "/usr/bin/time -p /tmp/ai-primary.sh proposal draft --reserved-model-budget 2000000 > /tmp/sp1-draft-${n}.out 2> /tmp/sp1-draft-${n}.err; echo \\$? > /tmp/sp1-draft-${n}.rc" > /dev/null 2>&1 &'\`
   then poll with a Bash loop (timeout 600000 ms per call) such as \`ssh ... 'for i in $(seq 1 55); do [ -f /tmp/sp1-draft-${n}.rc ] && break; sleep 10; done; cat /tmp/sp1-draft-${n}.rc 2>/dev/null || echo running'\`, repeating until the rc file exists (give up after 45 minutes total and report).
3. Read /tmp/sp1-draft-${n}.out and .err (stderr has the time -p "real" line). Then fetch the stored proposal: \`/tmp/ai-primary.sh proposal list\` and \`/tmp/ai-primary.sh proposal show <proposal-id>\` on the host.
4. Return: proposal_id, exit_code, duration_seconds, tokens_charged (from model provenance), codex_workdir (the -C directory Codex ran in), the proposal's root cause, intervention, chosen tier and tier audit, success metric, and proposal_json = the stored proposal payload verbatim. Put any failure output in errors (empty string if none).

Never run \`proposal decide\`, \`mark-applied\`, or apply anything. Never print secrets.
${ACCESS}`

const diagPrompt = f => `You are an independent investigator in an experiment. Root-cause one recurring agent problem from raw evidence, as a senior engineer who owns these agent setups would. Another system is drafting its own proposal for the same finding; you must stay BLIND to it: do not read the workflow SQLite store's proposals, proposal_events, proposal_drafts, review_sessions or model_runs tables, do not open /tmp/sp1-* files on the host, and do not run any \`agent-introspection proposal\` or \`candidates\` command.

Target: ${FINDING_DESC(f)}.

Investigate:
- Pull the failing calls (failure_signature, tool, command_head, targets, workdir, session/task) over the last 14 days and what the agent did on the next calls in the same session; did the task recover or derail? Quantify the real cost (extra calls, duration_ms, unclean task outcomes).
- Decide what kind of problem it is: environment/sandbox configuration, agent behaviour (e.g. guessing paths), a harness/tool defect, harmless exploration, or an artifact of how the detector normalises failure_class (read findings.py and the failure_class SQL).
- Where useful, look at the actual project directories on this Mac and harness configs to confirm (e.g. does the path the agent guessed exist under a different name? is a cache dir unwritable in the sandbox?).
- Recommend the single most effective intervention, preferring deterministic fixes (existing tool/config) over instructions, or none if it is not worth fixing.
Cite concrete evidence (query results, file paths). Distinguish observed facts from inferences.
${ACCESS}`

const judgePrompt = (f, draft, diag) => `You are the judge in an experiment testing whether Agent Introspection's automated proposal drafter produces proposals its owner would approve. You see the drafted proposal and an independent blind diagnosis of the same finding. Be skeptical of BOTH: verify their key factual claims against the facts store and files yourself before relying on either.

Finding: ${FINDING_DESC(f)}.

DRAFT (from the real \`proposal draft\` run; Codex gpt-5.5 high, read-only):
${JSON.stringify(draft, null, 1)}

INDEPENDENT DIAGNOSIS:
${JSON.stringify(diag, null, 1)}

Assess:
- root_cause_agreement between draft and diagnosis, and which is better supported where they differ.
- Check the draft's 2-5 most consequential claims against evidence (draft_claims_checked).
- intervention_fit: would it actually make this failure stop recurring, is it the right tier, is the success metric sensible?
- advisory_decision the owner (an expert running these agents) should make, with suggested_edits if minor edits would make it approvable. This is advisory only; do not record any decision.
- loop_defects: weaknesses in the improvement loop itself this case exposes (e.g. detector grouping unrelated failures, evidence pack missing workdirs, Codex drafting with no repository access, metric that can't detect the fix).
If the draft did not run, set the draft-dependent fields to no_draft and still list loop_defects.
${ACCESS}`

const findings = args.findings

// Drafts must be sequential; diagnoses run in parallel alongside them.
const draftResults = []
let chain = Promise.resolve()
const draftPromises = findings.map((f, i) => {
  chain = chain.then(() => agent(draftPrompt(f, i + 1), { label: `draft:${f.short}`, phase: 'Draft', schema: DRAFT_SCHEMA }))
  return chain
})
const diagPromises = findings.map(f => agent(diagPrompt(f), { label: `diagnose:${f.short}`, phase: 'Diagnose', schema: DIAG_SCHEMA }))

const results = await Promise.all(findings.map(async (f, i) => {
  const [draft, diag] = await Promise.all([draftPromises[i], diagPromises[i]])
  log(`${f.short}: draft ${draft && draft.ran ? 'done' : 'not produced'}, diagnosis ${diag ? diag.classification : 'missing'}`)
  const judge = await agent(judgePrompt(f, draft, diag), { label: `judge:${f.short}`, phase: 'Judge', schema: JUDGE_SCHEMA, effort: 'high' })
  return { finding: f, draft, diag, judge }
}))

return results
