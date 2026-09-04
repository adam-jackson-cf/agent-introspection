"""Pure E-Recurrence-1 reducer for immutable actionable finding candidates."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from experiments.dashboard_prototype.contracts import SUPPORTED_PRODUCERS, PrototypeContractError

_LONDON = ZoneInfo("Europe/London")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TOKEN = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class FindingEvent:
    """One immutable, versioned canonical activity member of a finding."""

    producer: str
    detector_id: str
    detector_version: int
    finding_fingerprint: str
    canonical_task_id: str
    event_id: str
    activity_version: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.producer not in SUPPORTED_PRODUCERS:
            raise PrototypeContractError("finding producer is unsupported")
        for value, label in (
            (self.detector_id, "detector ID"),
            (self.canonical_task_id, "canonical task ID"),
            (self.event_id, "event ID"),
        ):
            if not isinstance(value, str) or not _TOKEN.fullmatch(value):
                raise PrototypeContractError(f"finding {label} must be a privacy-safe token")
        if not _SHA256.fullmatch(self.finding_fingerprint):
            raise PrototypeContractError("finding fingerprint must be a SHA-256 digest")
        for number, label in (
            (self.detector_version, "detector version"),
            (self.activity_version, "activity version"),
        ):
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise PrototypeContractError(f"finding {label} must be positive")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise PrototypeContractError("finding event time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class FindingReductionBoundary:
    """Seven consecutive Europe/London calendar days, represented as UTC instants."""

    source_start: datetime
    source_end: datetime
    events: Iterable[FindingEvent] = ()

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
            for value in (self.source_start, self.source_end)
        ):
            raise PrototypeContractError("finding source bounds must be timezone-aware")
        start = self.source_start.astimezone(UTC)
        end = self.source_end.astimezone(UTC)
        start_london = start.astimezone(_LONDON)
        end_london = end.astimezone(_LONDON)
        if (
            start_london.timetz().replace(tzinfo=None) != datetime.min.time()
            or end_london.timetz().replace(tzinfo=None) != datetime.min.time()
        ):
            raise PrototypeContractError("finding source bounds must be London local midnights")
        if start_london.date().toordinal() + 7 != end_london.date().toordinal():
            raise PrototypeContractError(
                "finding source window must span exactly seven London days"
            )
        object.__setattr__(self, "source_start", start)
        object.__setattr__(self, "source_end", end)
        object.__setattr__(self, "events", tuple(self.events))


@dataclass(frozen=True, slots=True)
class FindingCandidate:
    """Immutable, privacy-safe M14 candidate derived from authoritative events."""

    detector_id: str
    detector_version: int
    finding_fingerprint: str
    occurrence_count: int
    canonical_task_count: int
    london_day_count: int
    event_ids: tuple[str, ...]
    event_versions: tuple[int, ...]
    source_start: datetime
    source_end: datetime

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.detector_id) or not _SHA256.fullmatch(
            self.finding_fingerprint
        ):
            raise PrototypeContractError("candidate identity is invalid")
        numeric_counts = (
            (self.detector_version, "detector version"),
            (self.occurrence_count, "occurrence count"),
            (self.canonical_task_count, "canonical task count"),
            (self.london_day_count, "London day count"),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value, _ in numeric_counts
        ):
            raise PrototypeContractError("candidate aggregate values must be integers")
        if self.detector_version < 1:
            raise PrototypeContractError("candidate detector version must be positive")
        if (
            self.occurrence_count < 1
            or self.occurrence_count != len(self.event_ids)
            or len(self.event_ids) != len(self.event_versions)
        ):
            raise PrototypeContractError(
                "candidate event identities must match positive occurrence count"
            )
        if (
            not 1 <= self.canonical_task_count <= self.occurrence_count
            or not 1 <= self.london_day_count <= self.occurrence_count
        ):
            raise PrototypeContractError(
                "candidate task and day counts must be bounded by occurrences"
            )
        if len(set(self.event_ids)) != len(self.event_ids) or any(
            not _TOKEN.fullmatch(value) for value in self.event_ids
        ):
            raise PrototypeContractError("candidate event identities must be unique safe tokens")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in self.event_versions
        ):
            raise PrototypeContractError("candidate event versions must be positive")
        FindingReductionBoundary(self.source_start, self.source_end)

    @property
    def immutable_id(self) -> str:
        """Return the deterministic immutable identity of this exact finding version."""
        payload = {
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "finding_fingerprint": self.finding_fingerprint,
            "event_ids": self.event_ids,
            "event_versions": self.event_versions,
            "source_start": self.source_start.astimezone(UTC).isoformat(),
            "source_end": self.source_end.astimezone(UTC).isoformat(),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return f"finding.{digest}"

    @property
    def version(self) -> int:
        """Candidates are immutable first versions; durable replacement is explicit."""
        return 1

    @property
    def actionable(self) -> bool:
        return (
            self.occurrence_count >= 3
            and self.canonical_task_count >= 2
            and self.london_day_count >= 2
        ) or (self.occurrence_count >= 5 and self.canonical_task_count >= 3)


def reduce_findings(*, boundary: FindingReductionBoundary) -> tuple[FindingCandidate, ...]:
    """Select global latest event versions, then reduce bounded M14 populations."""
    latest: dict[str, FindingEvent] = {}
    identities: set[tuple[str, int]] = set()
    for event in boundary.events:
        if not isinstance(event, FindingEvent):
            raise PrototypeContractError("finding reduction requires finding events")
        identity = (event.event_id, event.activity_version)
        if identity in identities:
            raise PrototypeContractError("duplicate finding event version identity")
        identities.add(identity)
        if (
            prior := latest.get(event.event_id)
        ) is None or event.activity_version > prior.activity_version:
            latest[event.event_id] = event
    grouped: dict[tuple[str, int, str], list[FindingEvent]] = {}
    for event in latest.values():
        if boundary.source_start < event.occurred_at.astimezone(UTC) <= boundary.source_end:
            grouped.setdefault(
                (event.detector_id, event.detector_version, event.finding_fingerprint), []
            ).append(event)
    candidates = []
    for (detector_id, detector_version, fingerprint), events in grouped.items():
        ordered = tuple(sorted(events, key=lambda item: (item.occurred_at, item.event_id)))
        candidates.append(
            FindingCandidate(
                detector_id,
                detector_version,
                fingerprint,
                len(ordered),
                len({item.canonical_task_id for item in ordered}),
                len({item.occurred_at.astimezone(_LONDON).date() for item in ordered}),
                tuple(item.event_id for item in ordered),
                tuple(item.activity_version for item in ordered),
                boundary.source_start,
                boundary.source_end,
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda item: (item.detector_id, item.detector_version, item.finding_fingerprint),
        )
    )
