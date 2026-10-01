"""Helpers shared by the command-line subcommand modules."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_introspection.config import AppConfig, load_config
from agent_introspection.workflow import connect_workflow

type Handler = Callable[[argparse.Namespace], dict[str, Any]]


def diagnostic(message: str) -> None:
    print(message, file=sys.stderr)


def read_json(source: str) -> dict[str, Any]:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
        value = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("input JSON is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("input JSON must contain an object")
    return value


def app_config(args: argparse.Namespace) -> AppConfig:
    return load_config(Path(args.config) if args.config is not None else None)


def open_store(args: argparse.Namespace) -> sqlite3.Connection:
    config = app_config(args)
    return connect_workflow(config.database.path, busy_timeout_ms=config.database.busy_timeout_ms)
