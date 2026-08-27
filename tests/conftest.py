from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ("pytester",)


_TIER_REQUIREMENTS = {
    "frozen_gate2_replay": "evidence_root",
    "clickhouse_integration": "evidence_root",
    "gate6_installed": "installed_matrix",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("gate-6")
    group.addoption(
        "--evidence-root",
        action="store",
        metavar="PATH",
        help=(
            "Evidence directory required by frozen Gate-2 replay and ClickHouse integration tiers."
        ),
    )
    group.addoption(
        "--installed-matrix",
        action="store",
        metavar="PATH",
        help="Installed-surface matrix file required by the Gate-6 installed tier.",
    )


def _required_path(config: pytest.Config, option: str, marker: str) -> Path:
    value = config.getoption(option)
    if not value:
        raise pytest.UsageError(f"-m {marker} requires --{option.replace('_', '-')}")

    path = Path(value)
    if not path.exists():
        raise pytest.UsageError(
            f"-m {marker} requires existing --{option.replace('_', '-')}: {path}"
        )
    return path


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Require tier inputs only when a selected item carries that tier marker."""
    selected_markers = {
        marker
        for item in items
        for marker in _TIER_REQUIREMENTS
        if item.get_closest_marker(marker) is not None
    }

    for marker in selected_markers:
        option = _TIER_REQUIREMENTS[marker]
        path = _required_path(config, option, marker)
        if option == "evidence_root" and not path.is_dir():
            raise pytest.UsageError(
                f"-m {marker} requires --evidence-root to be a directory: {path}"
            )
        if option == "installed_matrix" and not path.is_file():
            raise pytest.UsageError(f"-m {marker} requires --installed-matrix to be a file: {path}")
