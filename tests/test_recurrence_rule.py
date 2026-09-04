from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import PrototypeContractError
from experiments.dashboard_prototype.recurrence_rule import (
    ApplicabilityState,
    RuleAdherenceBoundary,
    RuleApplicability,
    RuleViolation,
    SatisfactionState,
    VersionedRule,
    reduce_rule_adherence,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)
SESSION_HASH = "sha256:" + "a" * 64
OTHER_SESSION_HASH = "sha256:" + "b" * 64


def rule(version="v1", order=1, source_time=NOW):
    return VersionedRule(
        "omp",
        "omp",
        SESSION_HASH,
        "rule:review",
        version,
        "trigger:merge",
        "action:review",
        "observation:action",
        source_time,
        order,
        f"evidence:{version}",
    )


def applicable(task, state=ApplicabilityState.APPLICABLE, version="v1", session=SESSION_HASH):
    return RuleApplicability(
        "omp",
        "omp",
        session,
        "rule:review",
        version,
        task,
        state,
        NOW,
        2,
        f"evidence:{task}:{session[-1]}",
    )


def observation(task, state, version="v1", action=None, authoritative=False):
    return RuleViolation(
        "omp",
        "omp",
        SESSION_HASH,
        "rule:review",
        version,
        task,
        state,
        action,
        authoritative,
        NOW,
        3,
        f"observation:{task}",
    )


def boundary(**changes):
    values = {
        "source_start": NOW - timedelta(seconds=1),
        "source_end": NOW + timedelta(seconds=1),
        "source_boundary": "bounded",
        "rules": (),
        "applicability": (),
        "violations": (),
    }
    values.update(changes)
    return RuleAdherenceBoundary(**values)


def test_unknown_observability_is_not_non_adherent_and_zero_withholds_percentage():
    result = reduce_rule_adherence(
        boundary=boundary(rules=(rule(),), applicability=(applicable("task:one"),))
    )
    assert (
        result[0].numerator,
        result[0].denominator,
        result[0].unknown_count,
        result[0].percentage,
    ) == (0, 0, 1, None)


def test_global_latest_version_and_canonical_task_identity():
    result = reduce_rule_adherence(
        boundary=boundary(
            rules=(rule("v1", 1), rule("v2", 2)),
            applicability=(
                applicable("task:one", version="v2"),
                applicable("task:one", version="v2", session=OTHER_SESSION_HASH),
            ),
            violations=(
                observation("task:one", SatisfactionState.SATISFIED, "v2", "action:review", True),
            ),
        )
    )
    assert [
        (row.rule_version, row.applicable_count, row.numerator, row.denominator) for row in result
    ] == [("v2", 1, 1, 1)]


def test_unbound_observable_claims_fail_closed():
    for state, action, authoritative in (
        (SatisfactionState.SATISFIED, None, False),
        (SatisfactionState.UNSATISFIED, "action:review", False),
    ):
        with pytest.raises(PrototypeContractError):
            reduce_rule_adherence(
                boundary=boundary(
                    rules=(rule(),),
                    applicability=(applicable("task:one"),),
                    violations=(
                        observation("task:one", state, action=action, authoritative=authoritative),
                    ),
                )
            )


def test_current_rule_version_can_predate_calculation_window():
    result = reduce_rule_adherence(
        boundary=boundary(
            rules=(rule("v1", 1, NOW - timedelta(days=2)),), applicability=(applicable("task:one"),)
        )
    )
    assert result[0].rule_version == "v1"
