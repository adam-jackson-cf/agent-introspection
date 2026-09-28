"""Redacted, typed Pipeline query results shared with the companion server."""

from __future__ import annotations

from typing import Literal, TypedDict

PipelineState = Literal[
    "Data", "No data", "Not applicable", "Unavailable", "Integrity failure", "Query/system error"
]
Scalar = str | int | float | None


class PipelineMetric(TypedDict):
    """One measurement, including its rate or percentile population."""

    label: str
    value: Scalar
    unit: str
    numerator: int | None
    denominator: int | None
    sampleCount: int | None


class PipelinePoint(TypedDict):
    """One ordered observation in a declared unit."""

    at: str
    value: float


class PipelineSeries(TypedDict):
    """A bounded fixed series; units must not share an incompatible axis."""

    name: str
    unit: str
    points: list[PipelinePoint]


class PipelinePanel(TypedDict):
    """A complete result; missing measurements are never represented as zero."""

    state: PipelineState
    population: str
    timeBasis: str
    rangeOperator: str
    metrics: list[PipelineMetric]
    columns: list[str]
    rows: list[list[Scalar]]
    series: list[PipelineSeries]
    reasons: list[str]
    provenance: dict[str, str]


class PipelineDeployment(TypedDict):
    """Implementation identities actually reported by a selected scanner."""

    fingerprint: str
    projectionId: Literal["agent-introspection.pipeline-observations"]
    projectionSha256: str


class PipelineImplementation(TypedDict):
    """Executing calculator and observed remote scanner implementation identities."""

    calculationId: Literal["agent-introspection.pipeline-dashboard"]
    calculationSha256: str
    deployments: list[PipelineDeployment]


class PipelineReport(TypedDict):
    """Results for one exact UTC range and evaluation time."""

    start: str
    end: str
    evaluatedAt: str
    implementation: PipelineImplementation
    panels: dict[str, PipelinePanel]
