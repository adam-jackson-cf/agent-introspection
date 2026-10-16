"""The onboarding command groups of the Agent Introspection CLI.

``signoz`` finds the SigNoz on this machine, ``producers`` identifies and explores the
services that send telemetry, ``harness`` previews and configures producer harnesses,
``dashboard`` runs the dashboard as a service, and ``doctor`` checks every onboarding
step.
"""

from __future__ import annotations

from typing import Any

from agent_introspection.cli_common import Handler
from agent_introspection.cli_dashboard import add_dashboard_parser, dashboard_command
from agent_introspection.cli_doctor import add_doctor_parser, doctor_command
from agent_introspection.cli_harness import add_harness_parser, harness_command
from agent_introspection.cli_producers import add_producers_parser, producers_command
from agent_introspection.cli_signoz import add_signoz_parser, signoz_command

ONBOARDING_HANDLERS: dict[str, Handler] = {
    "signoz": signoz_command,
    "producers": producers_command,
    "harness": harness_command,
    "dashboard": dashboard_command,
    "doctor": doctor_command,
}


def add_onboarding_parsers(commands: Any) -> None:
    add_signoz_parser(commands)
    add_producers_parser(commands)
    add_harness_parser(commands)
    add_dashboard_parser(commands)
    add_doctor_parser(commands)
