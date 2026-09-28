"""Immutable OTEL projection envelopes shared by Pipeline reducers."""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from agent_introspection.telemetry import DerivedEvent


def parse_timestamp(value: object) -> int:
    """Decode ClickHouse UInt64 JSON strings without floating-point conversion."""
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**64:
        raise ValueError("invalid immutable projection timestamp")
    return value


class ImmutableNumberError(ValueError):
    """An immutable observation contains an invalid numeric representation."""


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImmutableNumberError("missing or invalid immutable projection number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ImmutableNumberError("immutable projection number exceeds finite range") from exc
    if not math.isfinite(number):
        raise ImmutableNumberError("missing or invalid immutable projection number")
    return number


@dataclass(frozen=True)
class ImmutableEvent:
    strings: Mapping[str, str]
    numbers: Mapping[str, int | float]
    booleans: Mapping[str, bool]
    timestamp_ns: int

    def text(self, key: str, *, empty: bool = False) -> str:
        value = self.strings[key]
        if not isinstance(value, str) or (not empty and not value):
            raise ValueError("missing or invalid immutable projection text")
        return value

    def number(self, key: str) -> float:
        return _finite_number(self.numbers[key])

    def count(self, key: str) -> int:
        value = self.number(key)
        if value < 0 or value > 2**53 - 1 or int(value) != value:
            raise ValueError("invalid immutable projection count")
        return int(value)

    def nanoseconds(self, key: str, *, minimum: int = 1) -> int:
        value = self.text(key)
        digits = value.removeprefix("-")
        if not digits.isascii() or not digits.isdecimal():
            raise ValueError("invalid immutable projection nanoseconds")
        result = int(value)
        if not minimum <= result < 2**64:
            raise ValueError("invalid immutable projection nanoseconds")
        return result

    def same_physical(self, other: ImmutableEvent) -> bool:
        return (
            self.timestamp_ns == other.timestamp_ns
            and _typed_items(self.strings) == _typed_items(other.strings)
            and _typed_items(self.numbers) == _typed_items(other.numbers)
            and _typed_items(self.booleans) == _typed_items(other.booleans)
        )

    def validate_identity(self, scope: str) -> None:
        event = DerivedEvent(
            scope=self.text("event.scope"),
            entity_id=self.text("entity.id"),
            entity_version=self.count("entity.version"),
            event_sequence=self.count("event.sequence"),
            event_name=self.text("event.name"),
            timestamp_ns=self.timestamp_ns,
            attributes={},
        )
        if (
            event.scope != scope
            or event.entity_version < 1
            or event.event_id != self.text("event.id")
            or isinstance(self.timestamp_ns, bool)
            or not isinstance(self.timestamp_ns, int)
            or not 0 < self.timestamp_ns < 2**64
        ):
            raise ValueError("invalid immutable projection identity")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate immutable payload key")
        result[key] = value
    return result


def _reject_nonfinite(_: str) -> None:
    raise ImmutableNumberError("invalid immutable payload number")


def _typed_items(values: Mapping[str, object]) -> tuple[tuple[str, type[object], object], ...]:
    return tuple(
        sorted(
            (key, type(value), value.hex() if isinstance(value, float) else value)
            for key, value in values.items()
        )
    )


def _scalar_payload(value: object) -> dict[str, str | int | float | bool]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str)
        or isinstance(item, (Mapping, list))
        or item is None
        or not isinstance(item, (str, int, float, bool))
        for key, item in value.items()
    ):
        raise ValueError("invalid immutable payload structure")
    for item in value.values():
        if isinstance(item, float):
            _finite_number(item)
    return value


def decode_immutable_event(row: Mapping[str, Any]) -> ImmutableEvent:
    encoded = row.get("body")
    if not isinstance(encoded, str):
        raise ValueError("missing immutable payload envelope")
    try:
        payload = _scalar_payload(
            json.loads(
                encoded,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonfinite,
            )
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid immutable payload envelope") from exc
    timestamp = parse_timestamp(row.get("timestamp"))
    timestamp_ns = payload.pop("timestamp_ns", None)
    if (
        isinstance(timestamp_ns, bool)
        or not isinstance(timestamp_ns, int)
        or timestamp_ns != timestamp
    ):
        raise ValueError("immutable payload timestamp mismatch")
    strings = {key: value for key, value in payload.items() if isinstance(value, str)}
    booleans = {key: value for key, value in payload.items() if isinstance(value, bool)}
    numbers = {key: value for key, value in payload.items() if not isinstance(value, (str, bool))}
    projections = (
        ("strings", strings, str),
        ("number_bits", numbers, str),
        ("booleans", booleans, bool),
    )
    for column, expected, kind in projections:
        actual = row.get(column)
        if (
            not isinstance(actual, Mapping)
            or set(actual) != set(expected)
            or any(
                not isinstance(key, str) or not isinstance(value, kind)
                for key, value in actual.items()
            )
        ):
            raise ValueError("immutable payload map mismatch")
        if column == "number_bits":
            if any(
                value
                != str(int.from_bytes(struct.pack(">d", _finite_number(expected[key])), "big"))
                for key, value in actual.items()
            ):
                raise ValueError("immutable payload numeric map mismatch")
        elif actual != expected:
            raise ValueError("immutable payload map mismatch")
    return ImmutableEvent(strings, numbers, booleans, timestamp)


def read_immutable_events(rows: Sequence[Mapping[str, Any]], *, scope: str) -> list[ImmutableEvent]:
    unique: dict[str, ImmutableEvent] = {}
    for row in rows:
        event = decode_immutable_event(row)
        event.validate_identity(scope)
        event_id = event.text("event.id")
        if event_id in unique and not unique[event_id].same_physical(event):
            raise ValueError("divergent immutable projection event")
        unique[event_id] = event
    return list(unique.values())


@dataclass(slots=True)
class ManifestCache:
    """Successful manifest and canonical UUID validation within one lifecycle parse."""

    manifests: dict[str, frozenset[str]] = field(default_factory=dict)
    validated_ids: set[str] = field(default_factory=set)


def read_event_ids(
    event: ImmutableEvent,
    key: str,
    count_key: str,
    *,
    manifest_cache: ManifestCache | None = None,
) -> frozenset[str]:
    """Decode a complete, unique manifest of canonical DerivedEvent UUIDs."""
    encoded = event.text(key)
    if manifest_cache is not None and (cached := manifest_cache.manifests.get(encoded)) is not None:
        if len(cached) != event.count(count_key):
            raise ValueError("invalid immutable event population manifest")
        return cached
    values = json.loads(encoded)
    if (
        not isinstance(values, list)
        or any(
            not isinstance(value, str)
            or (
                (manifest_cache is None or value not in manifest_cache.validated_ids)
                and str(UUID(value)) != value
            )
            for value in values
        )
        or len(values) != len(set(values))
        or len(values) != event.count(count_key)
    ):
        raise ValueError("invalid immutable event population manifest")
    manifest = frozenset(values)
    if manifest_cache is not None:
        manifest_cache.manifests[encoded] = manifest
        manifest_cache.validated_ids.update(manifest)
    return manifest
