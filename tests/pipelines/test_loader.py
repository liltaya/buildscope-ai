"""Tests for bounded safe pipeline YAML loading"""

from pathlib import Path

import pytest

from buildscope.pipelines import (
    PipelineConfigError,
    load_pipeline_file,
    parse_pipeline_yaml,
)

ROOT = Path(__file__).resolve().parents[2]


def test_load_cpp_sample() -> None:
    definition = load_pipeline_file(ROOT / "examples/pipelines/cpp-quality.yaml")

    assert definition.name == "cpp-quality-pipeline"

    assert [stage.name for stage in definition.stages] == ["configure", "build", "tests"]

    assert definition.stages[1].depends_on == ("configure",)

    assert definition.stages[2].retry_count == 1


def test_load_python_sample() -> None:
    definition = load_pipeline_file(ROOT / "examples/pipelines/python-quality.yaml")

    assert definition.name == "python-quality-pipeline"

    assert definition.stages[2].depends_on == (
        "lint",
        "types",
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "name: test\nstages: []",
        ("name: test\nstages:\n  - name: test\n    command: pytest -q"),
    ],
)
def test_invalid_content(text: str) -> None:
    with pytest.raises(PipelineConfigError):
        parse_pipeline_yaml(text)


def test_yaml_syntax_error() -> None:
    with pytest.raises(
        PipelineConfigError,
        match="Invalid pipeline YAML",
    ):
        parse_pipeline_yaml("name: [oops\n")


def test_unsafe_yaml_tag_rejected(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist"

    payload = f"!!python/object/apply:os.system ['touch {marker}']\n"

    with pytest.raises(PipelineConfigError):
        parse_pipeline_yaml(payload)

    assert not marker.exists()


def test_file_too_large(tmp_path: Path) -> None:
    path = tmp_path / "large.yaml"

    path.write_text("a" * (256 * 1024 + 1))

    with pytest.raises(
        PipelineConfigError,
        match="exceeds 256 KiB",
    ):
        load_pipeline_file(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(
        PipelineConfigError,
        match="Cannot read pipeline file",
    ):
        load_pipeline_file(tmp_path / "missing.yaml")


def test_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(
        PipelineConfigError,
        match="UTF-8",
    ):
        load_pipeline_file(path)


def test_validation_error_has_field_path() -> None:
    with pytest.raises(
        PipelineConfigError,
        match=r"stages\.0\.command",
    ):
        parse_pipeline_yaml("name: quality\nstages:\n  - name: tests\n    command: pytest -q\n")
