from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent_introspection import producers

_DAY_NS = 86_400 * 1_000_000_000
_FIRST_NS = 1_791_000_000 * 1_000_000_000


@dataclass
class FakeService:
    spans: dict[str, int] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)
    span_keys: dict[tuple[str, str], int] = field(default_factory=dict)
    log_keys: dict[tuple[str, str], int] = field(default_factory=dict)
    span_rows: int = 0
    log_rows: int = 0


def _line(row: dict[str, object]) -> str:
    return json.dumps({
        key: str(value) if isinstance(value, int) else value for key, value in row.items()
    })


def _top(counts: dict[Any, int], limit: int) -> list[tuple[Any, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))[:limit]


class FakeClickHouse:
    def __init__(self, services: dict[str, FakeService]) -> None:
        self.services = services
        self.queries: list[str] = []

    def __call__(self, sql: str) -> str:
        self.queries.append(sql)
        match = re.search(r"(?:serviceName|::String) = '([^']*)'", sql)
        service = match[1] if match else None
        limit = int(m[1]) if (m := re.search(r"LIMIT (\d+)", sql)) else 0
        if "sum(span_rows)" in sql:
            return self._activity(service)
        found = self.services.get(service or "", FakeService())
        if "ARRAY JOIN" in sql:
            keys = found.span_keys if producers.SPAN_TABLE in sql else found.log_keys
            return self._keys(keys, limit)
        names = found.events if "event.name" in sql else found.spans
        rows = _top(names, limit)
        return "\n".join(
            _line({"name": name, "rows": count, "distinct_names": len(names)})
            for name, count in rows
        )

    def _activity(self, service: str | None) -> str:
        chosen = {
            name: item for name, item in self.services.items() if service is None or name == service
        }
        return "\n".join(
            _line({
                "service": name,
                "span_rows": item.span_rows,
                "log_rows": item.log_rows,
                "first_ns": _FIRST_NS,
                "last_ns": _FIRST_NS + _DAY_NS,
            })
            for name, item in chosen.items()
            if item.span_rows or item.log_rows
        )

    @staticmethod
    def _keys(keys: dict[tuple[str, str], int], limit: int) -> str:
        lines = []
        for kind in producers.KEY_KINDS:
            family = {key: rows for (each, key), rows in keys.items() if each == kind}
            lines += [
                _line({"family": kind, "key": key, "rows": rows, "distinct_names": len(family)})
                for key, rows in _top(family, limit)
            ]
        return "\n".join(lines)


def tool_producer() -> FakeService:
    return FakeService(
        spans={"Tool.execute": 40, "LLM.run": 10, "/Users/alice/build": 2, "/home/bob/build": 3},
        events={"api_request": 12, "user_prompt": 4},
        span_keys={
            ("string", "tool.name"): 40,
            ("string", "session.id"): 40,
            ("string", "tool.arguments"): 40,
            ("string", "message.id"): 40,
            ("number", "duration_ms"): 40,
            ("resource", "service.name"): 55,
        },
        log_keys={
            ("string", "event.name"): 16,
            ("string", "prompt"): 4,
            ("number", "input_tokens"): 12,
            ("bool", "success"): 12,
            ("resource", "service.name"): 16,
        },
        span_rows=55,
        log_rows=16,
    )


def fake_services() -> dict[str, FakeService]:
    return {
        "claude-code": FakeService(spans={"claude_code.interaction": 5}, span_rows=5, log_rows=50),
        "codex_exec": FakeService(spans={"session_task.turn": 900}, span_rows=900),
        "opencode": FakeService(spans={"Tool.execute": 5000}, span_rows=5000),
        "agent-introspection": FakeService(log_rows=30),
        "toolbox": tool_producer(),
        "telemetrygen": FakeService(span_rows=6),
    }
