"""Validate pipeline dependencies and produce a deterministic DAG order"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from pathlib import Path

from buildscope.pipelines.loader import PipelineConfigError, load_pipeline_file
from buildscope.pipelines.schema import PipelineDefinition, StageDefinition


class PipelineGraphError(PipelineConfigError):
    """The pipeline's stage dependency graph is invalid."""


@dataclass(frozen=True, slots=True)
class PipelinePlan:
    """Validated dependency order; does not execute any commands."""

    definition: PipelineDefinition
    ordered_stages: tuple[StageDefinition, ...]

    @property
    def stage_names(self) -> tuple[str, ...]:
        """Return stage names in dependency-safe execution order."""
        return tuple(stage.name for stage in self.ordered_stages)


def _find_cycle(
    stages: dict[str, StageDefinition],
) -> tuple[str, ...]:
    """Find a concrete cycle path using depth-first search."""
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> tuple[str, ...] | None:
        state[name] = 1  # Currently visiting.
        stack.append(name)

        for dependency in stages[name].depends_on:
            if state.get(dependency, 0) == 1:
                start = stack.index(dependency)
                return (*stack[start:], dependency)

            if state.get(dependency, 0) == 0:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle

        stack.pop()
        state[name] = 2  # Fully visited.
        return None

    for name in stages:
        if state.get(name, 0) == 0:
            cycle = visit(name)
            if cycle is not None:
                return cycle

    return ()


def build_pipeline_plan(
    definition: PipelineDefinition,
) -> PipelinePlan:
    """Reject invalid dependencies and topologically order the stages."""
    stages = {stage.name: stage for stage in definition.stages}
    indices = {stage.name: index for index, stage in enumerate(definition.stages)}

    remaining = {name: 0 for name in stages}
    dependents: dict[str, list[str]] = {name: [] for name in stages}

    # Validate edges and build the dependency graph.
    for stage in definition.stages:
        for dependency in stage.depends_on:
            if dependency == stage.name:
                raise PipelineGraphError(f"Stage '{stage.name}' cannot depend on itself.")

            if dependency not in stages:
                raise PipelineGraphError(
                    f"Stage '{stage.name}' depends on unknown stage '{dependency}'."
                )

            remaining[stage.name] += 1
            dependents[dependency].append(stage.name)

    # Stages with no unmet dependencies can run first.
    ready = [indices[name] for name, count in remaining.items() if count == 0]
    heapify(ready)

    ordered: list[StageDefinition] = []

    # Kahn's topological sorting algorithm.
    while ready:
        stage = definition.stages[heappop(ready)]
        ordered.append(stage)

        for dependent in dependents[stage.name]:
            remaining[dependent] -= 1

            if remaining[dependent] == 0:
                heappush(ready, indices[dependent])

    # In a cyclic graph, some stages never become ready.
    if len(ordered) != len(definition.stages):
        cycle = _find_cycle(stages)

        raise PipelineGraphError("Dependency cycle detected: " + " -> ".join(cycle))

    return PipelinePlan(
        definition=definition,
        ordered_stages=tuple(ordered),
    )


def load_pipeline_plan(path: str | Path) -> PipelinePlan:
    """Load YAML, validate its schema, then validate its dependency DAG."""
    return build_pipeline_plan(load_pipeline_file(path))
