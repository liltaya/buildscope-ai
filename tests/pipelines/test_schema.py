"""Validation tests for declarative pipeline definitions"""

import pytest
from pydantic import ValidationError

from buildscope.pipelines import (
    PipelineDefinition,
    StageDefinition,
)


def valid_stage() -> dict[str, object]:
    return {
        "name": "tests",
        "command": ["pytest", "-q"],
    }


def test_valid_pipeline_and_defaults() -> None:
    pipeline = PipelineDefinition.model_validate(
        {
            "name": "python-quality",
            "stages": [valid_stage()],
        }
    )

    stage = pipeline.stages[0]

    assert stage.command == ("pytest", "-q")
    assert stage.depends_on == ()
    assert stage.timeout_seconds == 300
    assert stage.retry_count == 0

    assert pipeline.model_dump(mode="json")["stages"][0]["command"] == ["pytest", "-q"]


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        [],
        ["pytest", "  "],
        ["pytest", "bad\x00arg"],
        [42],
    ],
)
def test_reject_invalid_command(
    command: object,
) -> None:
    with pytest.raises(ValidationError):
        StageDefinition.model_validate(
            {
                "name": "tests",
                "command": command,
            }
        )


@pytest.mark.parametrize(
    "timeout",
    [0, -1, 3601, "20", True],
)
def test_reject_invalid_timeout(
    timeout: object,
) -> None:
    with pytest.raises(ValidationError):
        StageDefinition.model_validate(
            {
                "name": "tests",
                "command": ["pytest"],
                "timeout_seconds": timeout,
            }
        )


@pytest.mark.parametrize(
    "retry",
    [-1, 11, "1", True],
)
def test_reject_invalid_retry(
    retry: object,
) -> None:
    with pytest.raises(ValidationError):
        StageDefinition.model_validate(
            {
                "name": "tests",
                "command": ["pytest"],
                "retry_count": retry,
            }
        )


def test_reject_duplicate_stage_names() -> None:
    with pytest.raises(
        ValidationError,
        match="stage names must be unique",
    ):
        PipelineDefinition.model_validate(
            {
                "name": "quality",
                "stages": [
                    valid_stage(),
                    valid_stage(),
                ],
            }
        )


def test_reject_duplicate_dependencies() -> None:
    with pytest.raises(
        ValidationError,
        match="depends_on contains duplicate",
    ):
        StageDefinition.model_validate(
            {
                "name": "tests",
                "command": ["pytest"],
                "depends_on": [
                    "build",
                    "build",
                ],
            }
        )


def test_reject_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        StageDefinition.model_validate(
            {
                "name": "tests",
                "command": ["pytest"],
                "shell": True,
            }
        )


def test_reject_empty_stages() -> None:
    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(
            {
                "name": "quality",
                "stages": [],
            }
        )


def test_dependencies_graph_validation_is_deferred() -> None:
    pipeline = PipelineDefinition.model_validate(
        {
            "name": "quality",
            "stages": [
                {
                    "name": "tests",
                    "command": ["pytest"],
                    "depends_on": ["unknown"],
                }
            ],
        }
    )

    assert pipeline.stages[0].depends_on == ("unknown",)
