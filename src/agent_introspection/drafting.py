"""Draft one intervention proposal with a bounded, read-only ``codex exec`` review.

``draft`` exports the next candidate (reserving a review session), asks Codex to
root-cause it from the evidence pack and audit the affected project's established
tools, and imports the structured answer through the same validated path as
``proposal create``. Codex runs in a read-only sandbox with its telemetry export
disabled, so drafting never changes a project or adds its own runs to the facts.
"""

from __future__ import annotations

import json
import shlex
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_introspection import candidates
from agent_introspection.facts import SqlRunner
from agent_introspection.interventions import CANONICAL_TIER_LABELS, InterventionType
from agent_introspection.proposals import (
    CLUSTER_TASK_RATE,
    CORRECTION_TASK_RATE,
    MIN_METRIC_WINDOW_DAYS,
    SUCCESS_METRICS,
    ProposalInput,
    create_proposal,
    immediate_transaction,
    metric_for_subject,
)
from agent_introspection.review import (
    PROPOSAL_EFFORT,
    PROPOSAL_LIMITS,
    PROPOSAL_MODEL,
    import_model_output,
    validate_model_output,
)

CODEX_TIMEOUT_SECONDS = 3600.0
SCHEMA_PLACEHOLDER = "<output-schema.json>"
# Codex exports its own OpenTelemetry to the same SigNoz; a drafting run must not
# become a `codex_exec` task in the facts it is reviewing.
TELEMETRY_OFF = (
    'otel.exporter="none"',
    'otel.trace_exporter="none"',
    'otel.metrics_exporter="none"',
)


class DraftingError(RuntimeError):
    """The Codex drafting run failed or produced no usable answer."""


@dataclass(frozen=True)
class CodexResult:
    returncode: int
    stdout: str
    stderr: str


type CodexRunner = Callable[[list[str], str], CodexResult]


def run_codex(command: list[str], prompt: str) -> CodexResult:
    """Run ``codex exec`` with the prompt on stdin."""
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
            timeout=CODEX_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DraftingError(f"codex exec could not complete: {exc}") from exc
    return CodexResult(completed.returncode, completed.stdout, completed.stderr)


def codex_command(cwd: Path, schema_path: str) -> list[str]:
    """Return the non-interactive, read-only, telemetry-free ``codex exec`` command."""
    overrides = [item for value in TELEMETRY_OFF for item in ("-c", value)]
    return [
        "codex",
        "exec",
        "--model",
        PROPOSAL_MODEL,
        "-c",
        f'model_reasoning_effort="{PROPOSAL_EFFORT}"',
        *overrides,
        "--sandbox",
        "read-only",
        "-C",
        str(cwd),
        "--json",
        "--output-schema",
        schema_path,
        "-",
    ]


def _string_list() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _metric_schema(metrics: list[str], harnesses: list[str]) -> dict[str, Any]:
    harness: dict[str, Any] = {"type": "string"}
    if harnesses:
        harness["enum"] = harnesses
    return _object(
        {
            "metric": {"type": "string", "enum": metrics},
            "harnesses": {"type": "array", "items": harness, "minItems": 1},
            "baseline_days": {"type": "integer", "minimum": MIN_METRIC_WINDOW_DAYS},
            "evaluation_days": {"type": "integer", "minimum": MIN_METRIC_WINDOW_DAYS},
            "max_ratio": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        }
    )


def _proposal_schema(
    candidate_ids: list[str], metrics: list[str], harnesses: list[str]
) -> dict[str, Any]:
    tier = _object(
        {
            "tier": {"type": "string", "enum": list(CANONICAL_TIER_LABELS)},
            "can_enforce": {"type": "boolean"},
            "reason_unavailable": {"type": ["string", "null"]},
        }
    )
    handoff = _object(
        {
            "skill_name": {"type": "string"},
            "workflow_owner": {"type": ["string", "null"]},
            "ordered_steps": _string_list(),
        }
    )
    return _object(
        {
            "finding_id": {"type": "string", "enum": candidate_ids},
            "root_cause": {"type": "string"},
            "trend_window": {"type": "string"},
            "occurrence_count": {"type": "integer"},
            "task_count": {"type": "integer"},
            "day_count": {"type": "integer"},
            "representative_evidence": _string_list(),
            "membership_rationale": {"type": "string"},
            "intervention_type": {"type": "string", "enum": [str(t) for t in InterventionType]},
            "scope": {"type": "string"},
            "target": {"type": "string"},
            "intended_change": {"type": "string"},
            "established_tool_audit": {
                "type": "array",
                "items": tier,
                "minItems": len(CANONICAL_TIER_LABELS),
                "maxItems": len(CANONICAL_TIER_LABELS),
            },
            "rejected_alternatives": _string_list(),
            "validation_criteria": _string_list(),
            "rollback_criteria": _string_list(),
            "predicted_success_metric": _metric_schema(metrics, harnesses),
            "create_skill_handoff": {"anyOf": [handoff, {"type": "null"}]},
        }
    )


def output_schema(review: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON Schema of the complete model output document for one review."""
    ids = list(review["ordered_candidate_ids"])
    subjects = [
        candidate.get("finding", {}).get("subject", {})
        for candidate in review["payload"]["candidates"]
    ]
    harnesses = sorted({str(name) for subject in subjects for name in subject.get("harnesses", [])})
    kinds = [metric_for_subject(subject) for subject in subjects]
    metrics = sorted({kind for kind in kinds if kind}) if all(kinds) else list(SUCCESS_METRICS)
    echo = {
        key: {"type": "integer" if key == "schema_version" else "string", "enum": [review[key]]}
        for key in (
            "session_id",
            "nonce",
            "schema_version",
            "payload_hash",
            "requested_model",
            "requested_effort",
        )
    }
    result = _object(
        {
            "candidate_id": {"type": "string", "enum": ids},
            "proposal": _proposal_schema(ids, metrics, harnesses),
        }
    )
    return _object(
        {
            **echo,
            "results": {
                "type": "array",
                "items": result,
                "minItems": len(ids),
                "maxItems": len(ids),
            },
        }
    )


_INSTRUCTIONS = f"""\
You are drafting one intervention proposal for a recurring agent problem: either
a tool-failure cluster (the finding subject has tool_family and failure_class) or
a repeated user correction in one project (the subject has project and
correction_kind). You are read-only: never edit, create, or delete files, never run
commands that change state, and never apply the intervention. Only investigate
and answer.

For each candidate in the review payload below:

1. Root cause. Reason from the candidate's evidence pack only (for example daily
   counts, projects, command shape, failure signatures, what the agent did next,
   task outcomes and labels, example tasks). State the most likely cause of the
   recurrence, not a restatement of the symptom.
2. Established-tool audit. The working directory is the project where the
   finding happened most. Inspect its established tools first (package scripts,
   Makefile/justfile, pyproject/tool configs, pre-commit and lint configs, CI,
   hooks, AGENTS.md, skills). Record established_tool_audit as exactly these
   three tiers in this order: {json.dumps(list(CANONICAL_TIER_LABELS))}. For
   each give can_enforce (true only if that tier can deterministically prevent
   or catch the failure) and reason_unavailable (null when can_enforce is true,
   otherwise a concrete reason). At most one tier may be true: the first tier
   that can enforce it.
3. Choose exactly one intervention_type:
   - the first tier that can enforce: established_tool, new_tool or
     bespoke_script, in that order;
   - otherwise, if a workflow skill owns the behavior: improve_skill, with
     create_skill_handoff naming that skill;
   - otherwise, if the behavior is a repeated ordered workflow with no owner:
     create_skill, with create_skill_handoff describing the ordered steps;
   - otherwise agents_guidance, with scope folder, project or cross-project and
     target "folder guidance", "project guidance" or "~/.codex/AGENTS.md".
   create_skill_handoff is null for every other type.
4. predicted_success_metric: {{"metric": "{CLUSTER_TASK_RATE}" for a failure
   cluster or "{CORRECTION_TASK_RATE}" for a repeated correction, "harnesses":
   the subset of the finding's harnesses the change affects, "baseline_days"
   and "evaluation_days": integers >= {MIN_METRIC_WINDOW_DAYS}, "max_ratio": a
   number in (0, 1]}}. {CLUSTER_TASK_RATE} is the share of tasks that hit the
   failure cluster; {CORRECTION_TASK_RATE} is the share of the project's
   labelled tasks whose next prompt was a correction of the finding's kind.
   Success means the share in the evaluation window after the change is
   applied is at most max_ratio times the share in the baseline window before.
5. Copy occurrence_count, task_count and day_count from the finding
   (occurrence_count, canonical_task_count, local_day_count), set finding_id to
   the candidate id, and put evidence references (signatures, example task ids)
   in representative_evidence. Give concrete validation and rollback criteria.

Answer with only the JSON document matching the output schema. Echo
session_id, nonce, schema_version, payload_hash, requested_model and
requested_effort exactly as given, with one result per candidate in the given
order. Keep the whole document under {PROPOSAL_LIMITS.max_output_characters}
characters.
"""


def build_prompt(review: dict[str, Any]) -> str:
    """Return the drafting instructions followed by the review envelope."""
    envelope = {
        key: review[key]
        for key in (
            "session_id",
            "nonce",
            "schema_version",
            "payload_hash",
            "requested_model",
            "requested_effort",
            "ordered_candidate_ids",
        )
    }
    return (
        f"{_INSTRUCTIONS}\nReview envelope:\n{json.dumps(envelope, sort_keys=True)}\n\n"
        f"Review payload:\n{json.dumps(review['payload'], sort_keys=True)}\n"
    )


def working_directory(review: dict[str, Any], fallback: Path) -> Path:
    """Return the candidate's project root when it exists locally, else ``fallback``."""
    for candidate in review["payload"]["candidates"]:
        root = candidate.get("project_root")
        if root:
            path = Path(str(root)).expanduser()
            if path.is_dir():
                return path
    return fallback


@dataclass
class CodexRun:
    thread_id: str | None = None
    message: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _record_event(run: CodexRun, event: dict[str, Any]) -> None:
    kind = event.get("type")
    if kind == "thread.started":
        run.thread_id = str(event.get("thread_id") or "") or None
    elif kind == "item.completed":
        item = event.get("item") or {}
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            run.message = item["text"]
    elif kind == "turn.completed":
        for key, value in (event.get("usage") or {}).items():
            if isinstance(value, int) and not isinstance(value, bool):
                run.usage[key] = run.usage.get(key, 0) + value
    elif kind in ("turn.failed", "error"):
        error = event.get("error")
        detail = error.get("message") if isinstance(error, dict) else event.get("message")
        run.errors.append(str(detail or kind))


def parse_events(stdout: str) -> CodexRun:
    """Read the ``codex exec --json`` JSONL stream: thread id, last message, usage."""
    run = CodexRun()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            _record_event(run, event)
    return run


def provenance(run: CodexRun) -> dict[str, Any]:
    """Model provenance for ``import_model_output`` from a parsed Codex run.

    Codex reports reasoning tokens inside ``output_tokens``, so only the input and
    output components are recorded (availability ``partial``) and ``token_count``
    is their sum; cached input tokens are part of ``input_tokens``.
    """
    if not run.thread_id:
        raise DraftingError("codex exec reported no thread id")
    input_tokens = run.usage.get("input_tokens")
    output_tokens = run.usage.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        raise DraftingError("codex exec reported no token usage")
    return {
        "model": PROPOSAL_MODEL,
        "effort": PROPOSAL_EFFORT,
        "trace_id": run.thread_id,
        "token_count": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def import_proposals(
    connection: sqlite3.Connection, document: dict[str, Any], provenance: dict[str, Any]
) -> list[str]:
    """Validate model output, import the review, and create its proposals atomically.

    The review import and its proposals commit together or not at all, so a failed
    proposal insert never leaves the review consumed without proposals.
    """
    with immediate_transaction(connection):
        results = validate_model_output(connection, document, provenance=provenance)
        proposal_inputs: list[ProposalInput] = []
        for result in results:
            payload = result.get("proposal")
            if not isinstance(payload, dict):
                raise ValueError("proposal result requires a proposal object")
            proposal_input = ProposalInput(**payload)
            if proposal_input.finding_id != result.get("candidate_id"):
                raise ValueError("proposal finding_id must match its reviewed candidate_id")
            finding = connection.execute(
                "SELECT trend_state FROM findings WHERE id = ?", (proposal_input.finding_id,)
            ).fetchone()
            if finding is None or finding[0] != "actionable":
                raise ValueError("only actionable findings can produce proposals")
            proposal_inputs.append(proposal_input)
        import_model_output(connection, document, provenance=provenance, outer_transaction=True)
        return [
            create_proposal(connection, value, outer_transaction=True) for value in proposal_inputs
        ]


@dataclass(frozen=True)
class DraftRequest:
    reserved_model_budget: int
    batch_id: str | None = None
    dry_run: bool = False


def _export(
    connection: sqlite3.Connection, run: SqlRunner, request: DraftRequest
) -> dict[str, Any]:
    return candidates.export(
        connection,
        run,
        reserved_model_budget=request.reserved_model_budget,
        batch_id=request.batch_id,
    )


def _dry_run(
    connection: sqlite3.Connection, run: SqlRunner, request: DraftRequest, fallback_cwd: Path
) -> dict[str, Any]:
    # Export against an in-memory copy, so a dry run reserves no session or budget.
    scratch = sqlite3.connect(":memory:")
    try:
        connection.backup(scratch)
        exported = _export(scratch, run, request)
    finally:
        scratch.close()
    if exported["status"] != "exported":
        return exported
    review = exported["review"]
    command = codex_command(working_directory(review, fallback_cwd), SCHEMA_PLACEHOLDER)
    return {
        "status": "dry_run",
        "review": review,
        "command": command,
        "command_line": shlex.join(command),
        "output_schema": output_schema(review),
        "prompt": build_prompt(review),
    }


def _output_document(result: CodexResult, parsed: CodexRun) -> dict[str, Any]:
    if result.returncode != 0 or parsed.errors:
        lines = result.stderr.strip().splitlines()
        detail = "; ".join(parsed.errors) or (lines[-1] if lines else f"exit {result.returncode}")
        raise DraftingError(f"codex exec failed: {detail}")
    if parsed.message is None:
        raise DraftingError("codex exec produced no agent message")
    try:
        document = json.loads(parsed.message)
    except json.JSONDecodeError as exc:
        raise ValueError("codex output is not a JSON document") from exc
    if not isinstance(document, dict):
        raise ValueError("codex output must be a JSON object")
    return document


def draft(
    connection: sqlite3.Connection,
    run: SqlRunner,
    request: DraftRequest,
    *,
    codex: CodexRunner = run_codex,
    fallback_cwd: Path | None = None,
) -> dict[str, Any]:
    """Export the next candidate, draft its proposal with Codex, and import it.

    With ``request.dry_run`` nothing is reserved or run: the result carries the review
    envelope, the exact ``codex exec`` command, its output schema, and the prompt.
    """
    cwd_fallback = fallback_cwd or Path.cwd()
    if request.dry_run:
        return _dry_run(connection, run, request, cwd_fallback)
    exported = _export(connection, run, request)
    if exported["status"] != "exported":
        return exported
    review = exported["review"]
    cwd = working_directory(review, cwd_fallback)
    with tempfile.TemporaryDirectory(prefix="agent-introspection-draft-") as directory:
        schema_path = Path(directory) / "output-schema.json"
        schema_path.write_text(json.dumps(output_schema(review)))
        result = codex(codex_command(cwd, str(schema_path)), build_prompt(review))
    parsed = parse_events(result.stdout)
    document = _output_document(result, parsed)
    model_provenance = provenance(parsed)
    proposal_ids = import_proposals(connection, document, model_provenance)
    return {
        "status": "created",
        "proposal_ids": proposal_ids,
        "review_session_id": review["session_id"],
        "cwd": str(cwd),
        "trace_id": model_provenance["trace_id"],
        "token_count": model_provenance["token_count"],
    }
