"""Load the canonical agent-project OpenTelemetry schema."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files


class ProjectSchemaError(ValueError):
    """The packaged agent-project schema is malformed."""


@dataclass(frozen=True, slots=True)
class AgentProjectSchema:
    attribute_keys: dict[str, str]
    metadata_states: frozenset[str]
    kind_values: frozenset[str]
    dashboard_attribute_keys: dict[str, str]


def _load_schema() -> AgentProjectSchema:
    schema_file = files("agent_introspection.schemas.otel").joinpath("agent-project.schema.json")
    raw = schema_file.read_text()
    data = json.loads(raw)
    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        raise ProjectSchemaError("attributes must be an object")
    attribute_keys: dict[str, str] = {}
    for name in ("id", "name", "root", "kind"):
        attribute = attributes.get(name)
        if not isinstance(attribute, dict) or attribute.get("required") is not True:
            raise ProjectSchemaError(f"{name} must be a required attribute")
        key = attribute.get("key")
        if not isinstance(key, str) or not key:
            raise ProjectSchemaError(f"{name} key must be non-empty text")
        attribute_keys[name] = key
    states = data.get("metadata_states")
    if not isinstance(states, list) or set(states) != {"absent", "complete", "invalid"}:
        raise ProjectSchemaError("metadata states must be absent, complete, and invalid")
    kind = attributes["kind"].get("enum")
    if not isinstance(kind, list) or set(kind) != {"git", "non_git"}:
        raise ProjectSchemaError("project kind must be git or non_git")
    dashboard = data.get("dashboard_attributes")
    if (
        not isinstance(dashboard, dict)
        or dashboard.get("paired") is not True
        or dashboard.get("id") != attribute_keys["id"]
        or dashboard.get("name") != attribute_keys["name"]
    ):
        raise ProjectSchemaError("dashboard attributes must pair the project ID and name")
    return AgentProjectSchema(
        attribute_keys=attribute_keys,
        metadata_states=frozenset(states),
        kind_values=frozenset(kind),
        dashboard_attribute_keys={"id": attribute_keys["id"], "name": attribute_keys["name"]},
    )


AGENT_PROJECT_SCHEMA = _load_schema()
