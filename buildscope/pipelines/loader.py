"""Load declarative pipeline YAML without running project code"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from buildscope.pipelines.schema import PipelineDefinition

MAX_PIPELINE_BYTES = 256 * 1024


class PipelineConfigError(ValueError):
    """A pipeline file cannot be read, parsed, or validated."""


def parse_pipeline_yaml(
    content: str,
) -> PipelineDefinition:
    """Parse YAML text and validate its pipeline structure."""
    if len(content.encode("utf-8")) > MAX_PIPELINE_BYTES:
        raise PipelineConfigError("Pipeline YAML exceeds 256 KiB.")

    try:
        data: Any = yaml.safe_load(content)

    except yaml.YAMLError as exc:
        raise PipelineConfigError(f"Invalid pipeline YAML: {exc}") from exc

    try:
        return PipelineDefinition.model_validate(data)

    except ValidationError as exc:
        errors = exc.errors(
            include_input=False,
            include_url=False,
        )

        details = "; ".join(
            (f"{'.'.join(str(segment) for segment in error['loc']) or 'pipeline'}: {error['msg']}")
            for error in errors
        )

        raise PipelineConfigError(f"Invalid pipeline configuration: {details}") from exc


def load_pipeline_file(
    path: str | Path,
) -> PipelineDefinition:
    """Read and validate a bounded UTF-8 pipeline YAML file."""
    source = Path(path).expanduser()

    try:
        with source.open("rb") as stream:
            raw = stream.read(MAX_PIPELINE_BYTES + 1)

    except OSError as exc:
        raise PipelineConfigError(f"Cannot read pipeline file {source}: {exc}") from exc

    if len(raw) > MAX_PIPELINE_BYTES:
        raise PipelineConfigError("Pipeline YAML exceeds 256 KiB.")

    try:
        content = raw.decode("utf-8")

    except UnicodeDecodeError as exc:
        raise PipelineConfigError("Pipeline YAML must be UTF-8 encoded.") from exc

    return parse_pipeline_yaml(content)
