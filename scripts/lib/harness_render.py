from __future__ import annotations

import json
import re
from pathlib import Path

from agent_introspection import facts

HARNESSES_MODULE = (
    Path(__file__).resolve().parents[2] / "dashboard" / "src" / "harnesses.generated.ts"
)
_PRINT_WIDTH = 80
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _fits(line: str) -> bool:
    return len(line) <= _PRINT_WIDTH


def _array(head: str, items: list[str], suffix: str) -> list[str]:
    line = f"{head} = [{', '.join(items)}]{suffix};"
    if _fits(line):
        return [line]
    return [f"{head} = [", *(f"  {item}," for item in items), f"]{suffix};"]


def _property(key: str, value: str) -> list[str]:
    name = key if _IDENTIFIER.match(key) else _string(key)
    line = f"  {name}: {value},"
    return [line] if _fits(line) else [f"  {name}:", f"    {value},"]


def _assignment(head: str, value: str) -> list[str]:
    line = f"{head} = {value};"
    return [line] if _fits(line) else [f"{head} =", f"  {value};"]


def render_harnesses(registry: facts.Registry | None = None) -> str:
    harnesses = (registry or facts.load_registry()).harnesses
    services = ", ".join(map(facts.sql_string, harnesses))
    lines = [
        *_array("export const HARNESSES", list(map(_string, harnesses)), " as const"),
        "export type Harness = (typeof HARNESSES)[number];",
        "",
        "export const HARNESS_LABEL: Record<Harness, string> = {",
        *(line for key, label in harnesses.items() for line in _property(key, _string(label))),
        "};",
        "",
        *_assignment("export const HARNESS_SERVICES_SQL", _string(services)),
    ]
    return "\n".join(lines) + "\n"
