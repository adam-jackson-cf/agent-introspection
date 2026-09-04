from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_terminal_cadence import (
    A03_TERMINAL_OUTCOME_SQL,
    A04_TERMINAL_CADENCE_SQL,
    TerminalCadenceProofRequest,
    TerminalObservation,
    TerminalSchedulePolicy,
    build_terminal_cadence_proof,
    oracle_a03_terminal_outcomes,
    oracle_a04_terminal_cadence,
    parse_a03_terminal_outcome_result,
    parse_a04_terminal_cadence_result,
    reduce_terminal_cadence,
)

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def policy(*, identity: str = "scheduler-v1", interval: timedelta = timedelta(minutes=5)):
    return TerminalSchedulePolicy(identity, interval, "UTC", T0)


DEFAULT_POLICY = policy()


def observation(identity: str, state: str, minutes: int) -> TerminalObservation:
    instant = T0 + timedelta(minutes=minutes)
    return TerminalObservation(identity, state, instant, instant)


def proof(
    rows: tuple[TerminalObservation, ...],
    schedule: TerminalSchedulePolicy | None = None,
    *,
    minutes: int = 10,
):
    return build_terminal_cadence_proof(
        TerminalCadenceProofRequest(
            run_id="run-1",
            provenance=EvidenceProvenance.FRESH_REAL,
            source_boundary="durable-terminal-window",
            observations=rows,
            policy=DEFAULT_POLICY if schedule is None else schedule,
            start=T0,
            end=T0 + timedelta(minutes=minutes),
            evaluated_at=T0 + timedelta(minutes=minutes),
        )
    )


def test_succeeded_failed_and_no_data_are_counted_terminal_outcomes():
    result = proof(
        (
            observation("one", "no_data", 5),
            observation("two", "failed", 10),
            observation("three", "succeeded", 15),
        ),
        minutes=15,
    )

    assert result.result is ExperimentResult.PROVEN
    assert result.metrics["succeeded_count"] == 1
    assert result.metrics["failed_count"] == 1
    assert result.metrics["no_data_count"] == 1
    assert result.metrics["observed_count"] == 3


def test_nonterminal_states_are_not_invented_as_terminal_outcomes():
    result = proof(
        (observation("partial", "partial", 5), observation("cancelled", "cancelled", 10))
    )

    assert result.result is ExperimentResult.BLOCKED
    assert result.blocked_boundaries == (
        "unsupported durable terminal state:cancelled",
        "unsupported durable terminal state:partial",
    )
    assert "partial_count" not in result.metrics
    assert "cancelled_count" not in result.metrics


def test_unknown_durable_state_blocks_without_inference():
    result = proof((observation("one", "succeeded", 5), observation("two", "unknown", 10)))

    assert result.result is ExperimentResult.BLOCKED
    assert result.blocked_boundaries == ("unsupported durable terminal state:unknown",)


def test_exact_stale_threshold_is_stale_and_boundary_is_deterministic():
    reduced = reduce_terminal_cadence(
        (observation("one", "succeeded", 10),),
        policy(),
        start=T0,
        end=T0 + timedelta(minutes=10),
        evaluated_at=T0 + timedelta(minutes=15),
    )

    assert reduced.stale_boundary == T0 + timedelta(minutes=10)
    assert reduced.freshness_seconds == 300
    assert reduced.is_stale is True


def test_missed_cadence_uses_the_latest_successful_completion():
    reduced = reduce_terminal_cadence(
        (observation("one", "succeeded", 5),),
        policy(),
        start=T0,
        end=T0 + timedelta(minutes=10),
        evaluated_at=T0 + timedelta(minutes=10),
    )

    assert reduced.counts.expected_slots == 2
    assert reduced.counts.missed_cadence == 0


def test_invalid_interval_blocks_instead_of_computing_cadence():
    result = proof((observation("one", "succeeded", 5),), policy(interval=timedelta(0)))

    assert result.result is ExperimentResult.BLOCKED
    assert result.blocked_boundaries == ("valid authoritative schedule interval/timezone",)


def test_missing_policy_identity_blocks_with_exact_boundary():
    result = proof((observation("one", "succeeded", 5),), policy(identity=""))

    assert result.result is ExperimentResult.BLOCKED
    assert result.blocked_boundaries == ("authoritative schedule policy identity",)


def test_same_bounded_inputs_produce_identical_proof_json():
    rows = (observation("one", "succeeded", 5), observation("two", "failed", 10))

    assert proof(rows).canonical_json() == proof(rows).canonical_json()


def test_recent_failure_does_not_refresh_an_old_successful_completion():
    reduced = reduce_terminal_cadence(
        (
            observation("success", "succeeded", 5),
            observation("recent-failure", "failed", 15),
        ),
        policy(),
        start=T0,
        end=T0 + timedelta(minutes=15),
        evaluated_at=T0 + timedelta(minutes=15),
    )

    assert reduced.counts.failed == 1
    assert reduced.freshness_seconds == 600
    assert reduced.counts.missed_cadence == 1
    assert reduced.is_stale is True


def test_no_data_is_terminal_but_not_a_successful_completion():
    result = proof((observation("empty", "no_data", 10),))

    assert result.result is ExperimentResult.FAILED
    assert result.metrics["no_data_count"] == 1
    assert result.metrics["succeeded_count"] == 0
    assert result.metrics["freshness_seconds"] is None


def test_running_is_nonterminal_and_blocks_terminal_population():
    result = proof((observation("in-progress", "running", 10),))

    assert result.result is ExperimentResult.BLOCKED
    assert result.blocked_boundaries == ("unsupported durable terminal state:running",)


def test_source_membership_excludes_start_and_includes_end_boundary():
    reduced = reduce_terminal_cadence(
        (
            observation("start", "succeeded", 0),
            observation("middle", "succeeded", 5),
            observation("end", "failed", 10),
        ),
        policy(),
        start=T0,
        end=T0 + timedelta(minutes=10),
        evaluated_at=T0 + timedelta(minutes=10),
    )

    assert reduced.counts.succeeded == 1
    assert reduced.counts.failed == 1
    assert reduced.counts.observed == 2


def test_a03_sql_is_event_identified_and_time_bounded():
    assert "attributes_string['event.id'] IN ({event_ids})" in A03_TERMINAL_OUTCOME_SQL
    assert "timestamp > {start:DateTime64(9, 'UTC')}" in A03_TERMINAL_OUTCOME_SQL
    assert "timestamp <= {end:DateTime64(9, 'UTC')}" in A03_TERMINAL_OUTCOME_SQL
    assert "uniqExactIf(" in A03_TERMINAL_OUTCOME_SQL
    assert "uniqExact(attributes_string['event.id'])" in A03_TERMINAL_OUTCOME_SQL


def test_a03_oracle_counts_each_supported_terminal_outcome_exactly_once():
    outcome = oracle_a03_terminal_outcomes(
        (
            observation("success", "succeeded", 5),
            observation("failure", "failed", 10),
            observation("empty", "no_data", 15),
        ),
        start=T0,
        end=T0 + timedelta(minutes=15),
    )
    assert not isinstance(outcome, tuple)

    assert outcome.succeeded_count == 1
    assert outcome.failed_count == 1
    assert outcome.no_data_count == 1
    assert outcome.observed_count == 3
    assert outcome.unclassified_terminal_state_count == 0


def test_a03_oracle_deduplicates_repeated_delivery_of_one_immutable_event():
    outcome = oracle_a03_terminal_outcomes(
        (
            observation("success", "succeeded", 5),
            observation("success", "succeeded", 5),
        ),
        start=T0,
        end=T0 + timedelta(minutes=10),
    )

    assert not isinstance(outcome, tuple)
    assert outcome.succeeded_count == 1
    assert outcome.observed_count == 1


def test_a03_oracle_blocks_unclassified_or_identityless_terminal_rows():
    outcome = oracle_a03_terminal_outcomes(
        (
            observation("known", "succeeded", 5),
            TerminalObservation(None, "unknown", T0 + timedelta(minutes=10), T0),
        ),
        start=T0,
        end=T0 + timedelta(minutes=10),
    )

    assert outcome == (
        "durable terminal identity authority",
        "unsupported durable terminal state:unknown",
    )


def test_a03_result_parser_preserves_integer_scalars_and_rejects_bool():
    parsed = parse_a03_terminal_outcome_result(
        {
            "succeeded_count": 1,
            "failed_count": 2,
            "no_data_count": 3,
            "observed_count": 6,
            "unclassified_terminal_state_count": 0,
        }
    )

    assert parsed.observed_count == 6
    with pytest.raises(ValueError, match="nonnegative integer"):
        parse_a03_terminal_outcome_result(
            {
                "succeeded_count": True,
                "failed_count": 0,
                "no_data_count": 0,
                "observed_count": 0,
                "unclassified_terminal_state_count": 0,
            }
        )


def test_a04_sql_is_event_identified_time_bounded_and_retains_schedule_identity():
    assert "attributes_string['event.id'] IN ({event_ids})" in A04_TERMINAL_CADENCE_SQL
    assert "timestamp > {start:DateTime64(9, 'UTC')}" in A04_TERMINAL_CADENCE_SQL
    assert "timestamp <= {end:DateTime64(9, 'UTC')}" in A04_TERMINAL_CADENCE_SQL
    assert "dashboard.schedule_policy_identity" in A04_TERMINAL_CADENCE_SQL
    assert "dashboard.schedule_timezone" in A04_TERMINAL_CADENCE_SQL


def test_a04_oracle_uses_authoritative_interval_for_scan_age_and_missed_cadence():
    cadence = oracle_a04_terminal_cadence(
        (observation("success", "succeeded", 5),),
        policy(),
        start=T0,
        end=T0 + timedelta(minutes=15),
        evaluated_at=T0 + timedelta(minutes=15),
    )

    assert cadence.scan_age_seconds == 600
    assert cadence.missed_cadence_count == 1
    assert cadence.terminal_scan_count == 1
    assert cadence.blocked_boundaries == ()


def test_a04_oracle_blocks_evaluation_before_the_terminal_window_ends():
    cadence = oracle_a04_terminal_cadence(
        (observation("success", "succeeded", 5),),
        policy(),
        start=T0,
        end=T0 + timedelta(minutes=10),
        evaluated_at=T0 + timedelta(minutes=5),
    )

    assert cadence.blocked_boundaries == ("ordered timezone-aware source/evaluation boundary",)


def test_a04_oracle_blocks_missing_schedule_and_unclassified_state():
    cadence = oracle_a04_terminal_cadence(
        (observation("unknown", "unknown", 5),),
        policy(identity=""),
        start=T0,
        end=T0 + timedelta(minutes=10),
        evaluated_at=T0 + timedelta(minutes=10),
    )

    assert cadence.scan_age_seconds is None
    assert cadence.missed_cadence_count is None
    assert cadence.blocked_boundaries == (
        "authoritative schedule policy identity",
        "unsupported durable terminal state:unknown",
    )


def test_a04_result_parser_keeps_timestamp_and_schedule_scalar_types():
    latest = T0 + timedelta(minutes=10)
    parsed = parse_a04_terminal_cadence_result(
        {
            "policy_identity": "scheduler-v1",
            "interval_seconds": 300,
            "timezone": "UTC",
            "anchor_time": T0.isoformat(),
            "latest_successful_completion": latest,
            "policy_identity_count": 1,
            "interval_seconds_count": 1,
            "timezone_count": 1,
            "anchor_time_count": 1,
        }
    )

    assert parsed.latest_successful_completion is latest
    assert parsed.interval_seconds == 300


def test_a04_result_parser_rejects_invalid_schedule_scalar_shapes_without_coercion():
    valid = {
        "policy_identity": "scheduler-v1",
        "interval_seconds": 300,
        "timezone": "UTC",
        "anchor_time": T0.isoformat(),
        "latest_successful_completion": T0,
        "policy_identity_count": 1,
        "interval_seconds_count": 1,
        "timezone_count": 1,
        "anchor_time_count": 1,
    }
    invalid_fields = (
        ("interval_seconds", True),
        ("interval_seconds", "300"),
        ("interval_seconds", 300.0),
        ("policy_identity", " "),
        ("timezone", "not/a-timezone"),
        ("anchor_time", "not-a-timestamp"),
        ("anchor_time", "2026-09-01T00:00:00"),
        ("latest_successful_completion", "2026-09-01T00:00:00Z"),
        ("latest_successful_completion", datetime(2026, 9, 1)),
    )

    for field, invalid_value in invalid_fields:
        row = {**valid, field: invalid_value}
        try:
            parse_a04_terminal_cadence_result(row)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{field}={invalid_value!r} must be rejected")
