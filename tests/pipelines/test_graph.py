"""Tests for pipeline dependency validation and deterministic ordering"""

from pathlib import Path

import pytest

from buildscope.pipelines import (
    PipelineConfigError,
    PipelineDefinition,
    PipelineGraphError,
    build_pipeline_plan,
    load_pipeline_plan,
    parse_pipeline_yaml,
)

ROOT = Path(__file__).resolve().parents[2]


def make_pipeline(
    *entries: tuple[str, tuple[str, ...]],
) -> PipelineDefinition:
    """Build a real schema-validated pipeline for graph tests."""
    return PipelineDefinition.model_validate(
        {
            "name": "quality",
            "stages": [
                {
                    "name": name,
                    "command": ["echo", name],
                    "depends_on": list(dependencies),
                }
                for name, dependencies in entries
            ],
        }
    )


def test_cpp_example_execution_order() -> None:
    plan = load_pipeline_plan(ROOT / "examples/pipelines/cpp-quality.yaml")

    assert plan.stage_names == (
        "configure",
        "build",
        "tests",
    )

    assert plan.ordered_stages[-1].retry_count == 1


def test_python_example_execution_order() -> None:
    plan = load_pipeline_plan(ROOT / "examples/pipelines/python-quality.yaml")

    assert plan.stage_names == (
        "lint",
        "types",
        "tests",
    )


def test_forward_references_are_valid() -> None:
    pipeline = make_pipeline(
        ("tests", ("build",)),
        ("build", ("configure",)),
        ("configure", ()),
    )

    assert build_pipeline_plan(pipeline).stage_names == (
        "configure",
        "build",
        "tests",
    )


def test_independent_stages_keep_declaration_order() -> None:
    pipeline = make_pipeline(
        ("lint", ()),
        ("docs", ()),
        ("types", ()),
    )

    assert build_pipeline_plan(pipeline).stage_names == (
        "lint",
        "docs",
        "types",
    )


def test_ready_stage_ties_use_declaration_order() -> None:
    pipeline = make_pipeline(
        ("configure", ()),
        ("build", ("configure",)),
        ("lint", ()),
    )

    assert build_pipeline_plan(pipeline).stage_names == (
        "configure",
        "build",
        "lint",
    )


def test_multiple_dependencies_must_be_satisfied() -> None:
    pipeline = make_pipeline(
        ("tests", ("lint", "types")),
        ("types", ()),
        ("lint", ()),
    )

    assert build_pipeline_plan(pipeline).stage_names == (
        "types",
        "lint",
        "tests",
    )


def test_single_stage_is_valid() -> None:
    pipeline = make_pipeline(
        ("tests", ()),
    )

    assert build_pipeline_plan(pipeline).stage_names == ("tests",)


def test_unknown_dependency_names_stage_and_target() -> None:
    pipeline = make_pipeline(
        ("tests", ("missing",)),
    )

    with pytest.raises(
        PipelineGraphError,
        match="Stage 'tests' depends on unknown stage 'missing'",
    ):
        build_pipeline_plan(pipeline)


def test_self_dependency_is_rejected() -> None:
    pipeline = make_pipeline(
        ("tests", ("tests",)),
    )

    with pytest.raises(
        PipelineGraphError,
        match="Stage 'tests' cannot depend on itself",
    ):
        build_pipeline_plan(pipeline)


def test_two_node_cycle_reports_path() -> None:
    pipeline = make_pipeline(
        ("build", ("tests",)),
        ("tests", ("build",)),
    )

    with pytest.raises(
        PipelineGraphError,
        match="build -> tests -> build",
    ):
        build_pipeline_plan(pipeline)


def test_three_node_cycle_reports_path() -> None:
    pipeline = make_pipeline(
        ("a", ("b",)),
        ("b", ("c",)),
        ("c", ("a",)),
    )

    with pytest.raises(
        PipelineGraphError,
        match="a -> b -> c -> a",
    ):
        build_pipeline_plan(pipeline)


def test_cycle_message_excludes_downstream_stage() -> None:
    pipeline = make_pipeline(
        ("later", ("a",)),
        ("a", ("b",)),
        ("b", ("a",)),
    )

    with pytest.raises(PipelineGraphError) as error:
        build_pipeline_plan(pipeline)

    assert "a -> b -> a" in str(error.value)
    assert "later" not in str(error.value)


def test_disconnected_cycle_is_found() -> None:
    pipeline = make_pipeline(
        ("lint", ()),
        ("a", ("b",)),
        ("b", ("a",)),
    )

    with pytest.raises(
        PipelineGraphError,
        match="cycle",
    ):
        build_pipeline_plan(pipeline)


def test_planning_does_not_modify_definition() -> None:
    pipeline = make_pipeline(
        ("tests", ("build",)),
        ("build", ()),
    )

    before = pipeline.model_dump(mode="json")
    plan = build_pipeline_plan(pipeline)

    assert plan.stage_names == (
        "build",
        "tests",
    )

    assert pipeline.model_dump(mode="json") == before

    assert tuple(stage.name for stage in pipeline.stages) == ("tests", "build")


def test_planning_is_deterministic() -> None:
    pipeline = make_pipeline(
        ("tests", ("lint", "types")),
        ("lint", ()),
        ("types", ()),
    )

    orders = {build_pipeline_plan(pipeline).stage_names for _ in range(20)}

    assert orders == {
        ("lint", "types", "tests"),
    }


def test_maximum_valid_chain() -> None:
    pipeline = make_pipeline(
        *(
            (
                f"s{i}",
                (f"s{i - 1}",) if i else (),
            )
            for i in range(100)
        )
    )

    plan = build_pipeline_plan(pipeline)

    assert len(plan.ordered_stages) == 100
    assert plan.stage_names[0] == "s0"
    assert plan.stage_names[-1] == "s99"


def test_planning_never_executes_commands(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "ran"

    pipeline = PipelineDefinition.model_validate(
        {
            "name": "quality",
            "stages": [
                {
                    "name": "suspicious",
                    "command": ["touch", str(marker)],
                }
            ],
        }
    )

    assert build_pipeline_plan(pipeline).stage_names == ("suspicious",)

    assert not marker.exists()


def test_invalid_schema_fails_before_graph_validation() -> None:
    with pytest.raises(
        PipelineConfigError,
        match="Invalid pipeline configuration",
    ):
        pipeline = parse_pipeline_yaml(
            "name: quality\nstages:\n  - name: tests\n    command: pytest -q\n"
        )

        build_pipeline_plan(pipeline)


def test_graph_error_is_pipeline_config_error() -> None:
    pipeline = make_pipeline(
        ("tests", ("missing",)),
    )

    with pytest.raises(PipelineConfigError):
        build_pipeline_plan(pipeline)


def test_stage_with_dependencies_can_be_listed_first() -> None:
    pipeline = make_pipeline(
        ("release", ("tests",)),
        ("tests", ("build",)),
        ("build", ()),
    )

    assert build_pipeline_plan(pipeline).stage_names == (
        "build",
        "tests",
        "release",
    )
