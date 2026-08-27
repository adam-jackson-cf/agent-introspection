from __future__ import annotations

from pathlib import Path

import pytest

_PLUGIN = Path(__file__).with_name("conftest.py").read_text()


def _configure(pytester: pytest.Pytester, test_source: str) -> None:
    pytester.makeconftest(_PLUGIN)
    pytester.makepyfile(test_source)


@pytest.mark.parametrize(
    ("markexpr", "marker", "expected"),
    [
        ("frozen_gate2_replay", "frozen_gate2_replay", pytest.ExitCode.USAGE_ERROR),
        (
            "frozen_gate2_replay or ordinary",
            "frozen_gate2_replay",
            pytest.ExitCode.USAGE_ERROR,
        ),
        ("not frozen_gate2_replay", "frozen_gate2_replay", pytest.ExitCode.OK),
        ("clickhouse_integration", "clickhouse_integration", pytest.ExitCode.USAGE_ERROR),
    ],
)
def test_evidence_tier_requires_existing_path_only_when_selected(
    pytester: pytest.Pytester, markexpr: str, marker: str, expected: pytest.ExitCode
) -> None:
    _configure(
        pytester,
        f"""
        import pytest

        @pytest.mark.{marker}
        def test_tier():
            assert True

        @pytest.mark.ordinary
        def test_ordinary():
            assert True
        """,
    )

    result = pytester.runpytest("-m", markexpr)

    assert result.ret == expected
    if expected == pytest.ExitCode.OK:
        result.assert_outcomes(passed=1, deselected=1)
    else:
        assert f"-m {marker} requires --evidence-root" in result.stderr.str()


def test_default_collection_requires_path_for_marked_item(pytester: pytest.Pytester) -> None:
    _configure(
        pytester,
        """
        import pytest

        @pytest.mark.gate6_installed
        def test_tier():
            assert True
        """,
    )

    result = pytester.runpytest()

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "-m gate6_installed requires --installed-matrix" in result.stderr.str()


def test_marked_item_runs_with_required_path(pytester: pytest.Pytester) -> None:
    _configure(
        pytester,
        """
        import pytest

        @pytest.mark.gate6_installed
        def test_tier():
            assert True
        """,
    )
    matrix = pytester.path / "matrix.json"
    matrix.write_text("{}")

    result = pytester.runpytest("--installed-matrix", str(matrix))

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1)


def test_unmarked_suite_does_not_require_tier_paths(pytester: pytest.Pytester) -> None:
    _configure(
        pytester,
        """
        def test_ordinary():
            assert True
        """,
    )

    result = pytester.runpytest()

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1)
