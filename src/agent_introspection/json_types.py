"""Shared JSON-shaped type aliases for dataclass contracts."""

from __future__ import annotations

from collections.abc import Mapping

type JsonObject = dict[str, object]
type JsonMapping = Mapping[str, object]
