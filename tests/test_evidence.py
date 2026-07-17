import json
from pathlib import Path

from agent_introspection.evidence import (
    enrich_from_session_jsonl,
    hydrate_allowlisted_fields,
    redact_text,
)


def test_hydration_is_allowlisted_and_redacts_multiple_secret_classes() -> None:
    evidence = hydrate_allowlisted_fields(
        source_reference="log:1",
        fields={
            "assistant_output": "token=abcdefghijk password: hunter2 sk-abcdefghijklmnop",
            "prompt": "must never persist",
        },
        allowed_fields=frozenset({"assistant_output"}),
    )
    assert evidence.correlation_status == "correlated"
    assert evidence.redacted_content is not None
    assert "must never persist" not in evidence.redacted_content
    assert "abcdefghijk" not in evidence.redacted_content
    assert "hunter2" not in evidence.redacted_content
    assert "sk-abcdefghijklmnop" not in evidence.redacted_content


def test_scanner_uncertainty_quarantines_without_raw_content() -> None:
    evidence = hydrate_allowlisted_fields(
        source_reference="log:2",
        fields={"assistant_output": "x" * 1_000_001},
        allowed_fields=frozenset({"assistant_output"}),
    )
    assert evidence.correlation_status == "quarantined"
    assert evidence.redacted_content is None


def test_session_jsonl_only_enriches_correlated_candidates(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "thread_id": "task-1",
                        "trace_id": "trace-1",
                        "assistant_output": "safe",
                        "sequence": 1,
                    }
                ),
                json.dumps(
                    {
                        "thread_id": "other",
                        "trace_id": "trace-2",
                        "assistant_output": "unrelated",
                    }
                ),
            ]
        )
    )
    correlated = enrich_from_session_jsonl(path, task_id="task-1", trace_id="trace-1", turn_id=None)
    pending = enrich_from_session_jsonl(path, task_id="missing", trace_id="trace-1", turn_id=None)
    assert correlated.correlation_status == "correlated"
    assert correlated.redacted_content is not None
    assert "unrelated" not in correlated.redacted_content
    assert pending.correlation_status == "pending"
    assert pending.redacted_content is None


def test_redaction_preserves_non_secret_content() -> None:
    assert redact_text("ordinary diagnostic code E123") == "ordinary diagnostic code E123"
