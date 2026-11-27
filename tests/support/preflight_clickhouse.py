from __future__ import annotations

import json

from agent_introspection import facts, preflight
from agent_introspection.facts import FactsError

ALL_GRANTS = "GRANT SELECT, INSERT, ALTER, CREATE, DROP ON *.* TO default\n"


def _columns(names: tuple[str, ...], resource: str = "JSON(max_dynamic_paths=100)") -> str:
    return "\n".join(
        json.dumps({"name": name, "type": resource if name == "resource" else "String"})
        for name in names
    )


def fake_clickhouse(
    *,
    version: str = "25.5.6.14",
    span_columns: tuple[str, ...] = preflight.SPAN_COLUMNS,
    grants: dict[str, str] | None = None,
    show_grants: str = ALL_GRANTS,
    database_exists: bool = False,
) -> tuple[list[str], facts.SqlRunner]:
    """Answer each preflight statement; CHECK GRANT is unsupported unless given."""
    executed: list[str] = []

    def run(sql: str) -> str:
        executed.append(sql)
        answers = {
            "SELECT version()": version,
            "system.databases": "1" if database_exists else "0",
            "table = 'distributed_signoz_index_v3'": _columns(span_columns),
            "table = 'distributed_logs_v2'": _columns(preflight.LOG_COLUMNS),
            "SHOW GRANTS": show_grants,
            "GROUP BY source, service": json.dumps({
                "source": "spans",
                "service": "claude-code",
                "rows": "3",
            }),
        }
        if sql.startswith("CHECK GRANT"):
            if grants is None:
                raise FactsError("Code: 62. DB::Exception: Syntax error")
            return grants.get(sql.split(" ON ")[0].removeprefix("CHECK GRANT "), "1")
        return next((answer for key, answer in answers.items() if key in sql), "")

    return executed, run
